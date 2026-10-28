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
from verdant.operations import OperationService
from verdant.persistence import make_sessions
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


@flow(name="verdant-artifact-verification", persist_result=False, log_prints=False, retries=0)
def verify_artifact(namespace_id: str, operation_id: str, epoch: int):
    sessions = make_sessions(os.environ["VERDANT_DATABASE_URL"])
    s3 = None
    try:
        actor = service_principal(sessions)
        namespace, operation = UUID(namespace_id), UUID(operation_id)
        operations = OperationService(sessions)
        dispatch = operations.workflow_dispatch(actor, namespace, operation, epoch)
        provider_run = get_run_context().flow_run
        if (
            provider_run.deployment_id != dispatch.deployment_id
            or provider_run.idempotency_key != dispatch.invocation_key
        ):
            raise Conflict("Worker flow does not match persisted workflow admission")
        operations.complete_workflow_submission(actor, namespace, operation, epoch, provider_run.id)
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
