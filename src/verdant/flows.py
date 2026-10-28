"""Prefect execution entry point; logical results remain in Verdant PostgreSQL."""

import os
from uuid import UUID

import boto3
import httpx
from botocore.config import Config
from prefect import flow
from prefect.context import get_run_context

from verdant.access_tokens import AccessTokenSettings, AccessTokenVerifier
from verdant.domain import Conflict
from verdant.identity import IdentityService
from verdant.ingestion import IngestionService
from verdant.ingestion_maintenance import IngestionMaintenance
from verdant.ingestion_runner import IngestionRunner
from verdant.operations import OperationService
from verdant.persistence import make_sessions
from verdant.prefect_submission import parameters_match
from verdant.storage import S3Storage, StorageUnavailable
from verdant.verification import VerificationRunner


class WorkerIdentityUnavailable(Exception):
    """Worker identity cannot be established; no credentials enter error output."""


def service_principal(sessions):
    try:
        issuer = os.environ["VERDANT_OIDC_ISSUER"]
        client_id = os.environ["VERDANT_WORKER_CLIENT_ID"]
        settings = AccessTokenSettings(
            issuer,
            os.environ["VERDANT_API_AUDIENCE"],
            frozenset({client_id}),
            allow_insecure_http=os.environ.get("VERDANT_INSECURE_LOCAL_HTTP") == "1",
        )
        with httpx.Client(timeout=3, trust_env=False) as client:
            response = client.post(
                issuer + "/protocol/openid-connect/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": os.environ["VERDANT_WORKER_CLIENT_SECRET"],
                },
            )
            response.raise_for_status()
        claims = AccessTokenVerifier(settings).verify(response.json()["access_token"])
        return IdentityService(sessions).resolve(claims["iss"], claims["sub"], kind="service")
    except Exception:
        raise WorkerIdentityUnavailable(
            "Worker service identity verification unavailable"
        ) from None


def admitted_worker(sessions, namespace, operation, epoch, allowed_kinds):
    actor = service_principal(sessions)
    operations = OperationService(sessions)
    state = operations.get(actor, namespace, operation)
    if state.kind not in allowed_kinds:
        raise Conflict("Operation kind does not match this fixed worker deployment")
    dispatch = operations.workflow_dispatch(actor, namespace, operation, epoch)
    provider_run = get_run_context().flow_run
    if (
        dispatch.deployment_id is None
        or provider_run.deployment_id != dispatch.deployment_id
        or provider_run.idempotency_key != dispatch.invocation_key
        or not parameters_match(
            provider_run.parameters,
            {
                "namespace_id": str(namespace),
                "operation_id": str(operation),
                "epoch": epoch,
            },
        )
    ):
        raise Conflict("Worker flow does not match persisted workflow admission")
    operations.complete_workflow_submission(actor, namespace, operation, epoch, provider_run.id)
    return actor, operations


@flow(name="verdant-artifact-verification", persist_result=False, log_prints=False, retries=0)
def verify_artifact(namespace_id: str, operation_id: str, epoch: int):
    sessions = make_sessions(os.environ["VERDANT_DATABASE_URL"])
    s3 = None
    try:
        namespace, operation = UUID(namespace_id), UUID(operation_id)
        actor, operations = admitted_worker(
            sessions, namespace, operation, epoch, {"verify_sample_artifact"}
        )
        s3 = boto3.client(
            "s3",
            endpoint_url=os.environ["VERDANT_S3_ENDPOINT"],
            region_name="us-east-1",
            aws_access_key_id=os.environ["VERDANT_S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["VERDANT_S3_SECRET_KEY"],
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=3,
                read_timeout=5,
                retries={"max_attempts": 2},
            ),
        )
        result = VerificationRunner(sessions, s3, os.environ["VERDANT_S3_BUCKET"]).run(
            actor, namespace, operation, epoch
        )
        return {"operation_id": str(result.id), "epoch": result.epoch, "status": result.status}
    finally:
        if s3 is not None:
            s3.close()
        sessions.kw["bind"].dispose()


@flow(name="verdant-ingestion", persist_result=False, log_prints=False, retries=0)
def ingest_operations(namespace_id: str, operation_id: str, epoch: int):
    sessions = make_sessions(os.environ["VERDANT_DATABASE_URL"])
    storage = None
    try:
        namespace, operation = UUID(namespace_id), UUID(operation_id)
        actor, operations = admitted_worker(
            sessions, namespace, operation, epoch, {"ingest_upload", "ingest_cleanup"}
        )
        state = operations.get(actor, namespace, operation)
        if state.kind == "ingest_upload" and state.status == "cancellation_requested":
            IngestionService(sessions, bucket="unconfigured").ensure_cleanup(
                actor, namespace, operation, epoch
            )
            return {"operation_id": str(state.id), "epoch": state.epoch, "status": state.status}
        if state.status in {"succeeded", "cancelled"}:
            return {"operation_id": str(state.id), "epoch": state.epoch, "status": state.status}
        storage = S3Storage.from_env()
        if storage is None:
            raise StorageUnavailable("Worker ingestion storage is not configured")
        if state.kind == "ingest_cleanup":
            result = IngestionMaintenance(sessions, storage).run(actor, namespace, operation, epoch)
            if result.status == "failed":
                raise Conflict(
                    "Cleanup needs provider reconciliation; inspect parent and retry maintenance"
                )
        else:
            runner = IngestionRunner(sessions, storage)
            for _ in range(4):
                state = operations.get(actor, namespace, operation)
                if state.epoch != epoch:
                    raise Conflict("Operation attempt is stale")
                if state.status == "cancellation_requested":
                    runner.ingestion.ensure_cleanup(actor, namespace, operation, epoch)
                    result = state
                    break
                result = runner.run(actor, namespace, operation, epoch, limit=25).operation
                if result.status == "succeeded":
                    break
            else:
                raise Conflict("Ingestion exceeded its admitted bounded pass")
        return {"operation_id": str(result.id), "epoch": result.epoch, "status": result.status}
    finally:
        if storage is not None:
            storage.close()
        sessions.kw["bind"].dispose()
