"""Actual local ingestion/maintenance process qualification; preserve all existing services.

Only the fixed loopback Prefect/S3 and an explicit *_test database are eligible.
Private local Keycloak inputs are reused without printing or changing credentials.
"""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import WorkPoolCreate
from prefect.client.schemas.filters import FlowRunFilter, FlowRunFilterIdempotencyKey
from prefect.workers.process import ProcessWorker
from qualify_prefect_execution import configure

from verdant.access_tokens import AccessTokenSettings, AccessTokenVerifier
from verdant.dispatch import WorkflowDispatcher
from verdant.flows import ingest_operations, service_principal, verify_artifact
from verdant.identity import IdentityService
from verdant.ingestion import (
    IngestionService,
    SourceDeclaration,
    SourceRegistrationRequest,
    UploadDeclaration,
    UploadRequest,
)
from verdant.operations import OperationService
from verdant.persistence import NamespaceRow, make_sessions
from verdant.storage import ObjectDigest, ObjectMissing, S3Storage, configured_source_key

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = Path("/tmp/verdant-issue14-integrated-upload-receipt.json")
POOL = "verdant-issue14-integrated"
SOURCE_MODE = False


async def ensure_pool():
    async with get_client() as client:
        await client.create_work_pool(
            WorkPoolCreate(
                name=POOL,
                type="process",
                base_job_template=ProcessWorker.get_default_base_job_template(),
            ),
            overwrite=True,
        )


def actor_and_namespace(sessions):
    issuer = os.environ["VERDANT_OIDC_ISSUER"]
    with httpx.Client(timeout=3, trust_env=False) as client:
        response = client.post(
            issuer + "/protocol/openid-connect/token",
            data={
                "client_id": "verdant-worker",
                "client_secret": os.environ["VERDANT_WORKER_CLIENT_SECRET"],
                "grant_type": "client_credentials",
            },
        )
        response.raise_for_status()
    claims = AccessTokenVerifier(
        AccessTokenSettings(
            issuer,
            "verdant-api",
            frozenset({"verdant-worker"}),
            allow_insecure_http=True,
        )
    ).verify(response.json()["access_token"])
    identities = IdentityService(sessions)
    principal = identities.provision(claims["iss"], claims["sub"], kind="service")
    ns = uuid4()
    with sessions.begin() as session:
        session.add(NamespaceRow(id=ns, name="Issue14 native ingestion qualification"))
    identities.grant(principal, ns, "write")
    return identities.resolve(claims["iss"], claims["sub"], kind="service"), ns


async def tick(sessions, actor, ns, receipt):
    async with get_client() as client:
        result = await WorkflowDispatcher(
            sessions,
            client,
            UUID(receipt["verification_deployment_id"]),
            UUID(receipt["ingestion_deployment_id"]),
        ).tick(actor, ns, limit=100)
    return result


def prepare(
    sessions,
    storage,
    *,
    verification_name="issue14-integrated-regression",
    ingestion_name="issue14-integrated-local",
):
    actor, ns = actor_and_namespace(sessions)
    asyncio.run(ensure_pool())
    verification = verify_artifact.to_deployment(
        name=verification_name,
        work_pool_name=POOL,
        job_variables={"working_dir": str(ROOT)},
    ).apply()
    ingestion = ingest_operations.to_deployment(
        name=ingestion_name,
        work_pool_name=POOL,
        job_variables={"working_dir": str(ROOT)},
    ).apply()
    service = IngestionService(sessions, bucket=storage.settings.bucket, storage=storage)
    body = b"Actual native Prefect ingestion and retained final artifact\n"
    digest = hashlib.sha256(body).hexdigest()
    if SOURCE_MODE:
        key = configured_source_key(storage.settings.source_prefix, ns, "source.txt")
        storage.client.put_object(Bucket=storage.settings.bucket, Key=key, Body=body)
        batch = service.admit_source(
            actor,
            ns,
            SourceRegistrationRequest(
                request_key=str(uuid4()),
                items=[
                    SourceDeclaration(
                        source_key="native-source-qualification",
                        source_object="source.txt",
                        modality="text",
                        sha256=digest,
                        size_bytes=len(body),
                        metadata={"qualification": "real native source process"},
                    )
                ],
            ),
        )
        item = batch.items[0]
    else:
        batch = service.admit(
            actor,
            ns,
            UploadRequest(
                request_key=str(uuid4()),
                items=[
                    UploadDeclaration(
                        source_key="native-qualification",
                        modality="text",
                        sha256=digest,
                        size_bytes=len(body),
                        metadata={"qualification": "real native process"},
                    )
                ],
            ),
        )
        item = batch.items[0]
        service.initiate_upload(actor, ns, batch.operation.id, item.id, 1)
        part = service.sign_upload_part(actor, ns, batch.operation.id, item.id, 1, 1)
        with httpx.Client(timeout=10, trust_env=False) as client:
            client.put(part.url, content=body).raise_for_status()
        service.complete_upload(actor, ns, batch.operation.id, item.id, 1)
    sources = sorted(path.name for path in (ROOT / "src/verdant").glob("*.py"))
    receipt = {
        "namespace_id": str(ns),
        "principal_id": str(actor.id),
        "operation_id": str(batch.operation.id),
        "item_id": str(item.id),
        "sha256": digest,
        "size_bytes": len(body),
        "verification_deployment_id": str(verification),
        "ingestion_deployment_id": str(ingestion),
        "pool": POOL,
        "database": "verdant_issue14_integrated_test",
        "mode": "source" if SOURCE_MODE else "upload",
        "source_sha256": {
            name: hashlib.sha256((ROOT / "src/verdant" / name).read_bytes()).hexdigest()
            for name in sources
        },
    }
    asyncio.run(tick(sessions, actor, ns, receipt))
    RECEIPT.write_text(json.dumps(receipt) + "\n")
    print(
        json.dumps(
            {
                "operation_id": receipt["operation_id"],
                "namespace_id": str(ns),
                "status": "submitted",
            }
        )
    )


async def provider_proof(sessions, actor, ns, receipt, child_id):
    operations = OperationService(sessions)
    results = []
    async with get_client() as client:
        for operation in [UUID(receipt["operation_id"]), child_id]:
            dispatch = operations.workflow_dispatch(actor, ns, operation, 1)
            run = await client.read_flow_run(UUID(dispatch.provider_reference))
            matches = await client.read_flow_runs(
                flow_run_filter=FlowRunFilter(
                    idempotency_key=FlowRunFilterIdempotencyKey(any_=[dispatch.invocation_key])
                ),
                limit=2,
            )
            assert len(matches) == 1 and run.state.is_completed()
            assert run.deployment_id == UUID(receipt["ingestion_deployment_id"])
            assert run.parameters == {
                "namespace_id": str(ns),
                "operation_id": str(operation),
                "epoch": 1,
            }
            assert run.idempotency_key == dispatch.invocation_key
            results.append(
                {
                    "operation_id": str(operation),
                    "provider_run_id": str(run.id),
                    "state": run.state.name,
                    "matching_runs": len(matches),
                }
            )
    return results


def observe(sessions, storage):
    receipt = json.loads(RECEIPT.read_text())
    actor, ns = service_principal(sessions), UUID(receipt["namespace_id"])
    assert str(actor.id) == receipt["principal_id"]
    asyncio.run(tick(sessions, actor, ns, receipt))
    service = IngestionService(sessions, bucket=storage.settings.bucket, storage=storage)
    batch = service.get(actor, ns, UUID(receipt["operation_id"]))
    child = (
        OperationService(sessions).get(actor, ns, batch.cleanup_operation_id)
        if batch.cleanup_operation_id
        else None
    )
    status = {
        "ingestion": batch.operation.status,
        "cleanup": child.status if child else "not_admitted",
    }
    if batch.operation.status != "succeeded" or child is None or child.status != "succeeded":
        print(json.dumps(status))
        return
    item = batch.items[0]
    assert item.state == "accepted" and item.sha256 == receipt["sha256"]
    storage.verify(f"{ns}/artifacts/{item.artifact_id}", ObjectDigest(item.sha256, item.size_bytes))
    try:
        storage.size(f"{ns}/staging/{item.id}")
    except ObjectMissing:
        pass
    else:
        raise RuntimeError("Native maintenance did not remove owned staging")
    if SOURCE_MODE:
        key = configured_source_key(storage.settings.source_prefix, ns, "source.txt")
        storage.verify(key, ObjectDigest(item.sha256, item.size_bytes))
        receipt["original_source_retained"] = True
    proof = asyncio.run(provider_proof(sessions, actor, ns, receipt, child.id))
    receipt.update(
        status=status,
        cleanup_operation_id=str(child.id),
        provider_runs=proof,
        final_bytes_verified=True,
        owned_staging_removed=True,
    )
    RECEIPT.write_text(json.dumps(receipt) + "\n")
    print(json.dumps({"status": status, "provider_runs": proof, "final_bytes_verified": True}))


def main():
    global SOURCE_MODE, RECEIPT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--observe", action="store_true")
    args = parser.parse_args()
    SOURCE_MODE = args.sources
    if SOURCE_MODE:
        RECEIPT = Path("/tmp/verdant-issue14-integrated-source-receipt.json")
    os.environ["VERDANT_S3_SOURCE_PREFIX"] = "qualification/issue14-integrated-sources"
    url = configure()
    database = "verdant_issue14_integrated_test"
    if not url.endswith("/" + database):
        raise RuntimeError("This qualifier requires its explicit isolated ingestion proof database")
    os.environ["PREFECT_WORKER_WEBSERVER_PORT"] = "18424"
    os.environ["PREFECT_SERVER_ANALYTICS_ENABLED"] = "false"
    if args.worker:
        executable = ROOT / ".venv/bin/prefect"
        os.execv(
            executable,
            [
                str(executable),
                "worker",
                "start",
                "--pool",
                POOL,
                "--type",
                "process",
                "--limit",
                "1",
                "--with-healthcheck",
            ],
        )
    sessions, storage = make_sessions(url), S3Storage.from_env()
    try:
        if args.observe:
            observe(sessions, storage)
        else:
            if RECEIPT.exists():
                raise RuntimeError(
                    "Owned qualification receipt exists; observe it before starting another"
                )
            prepare(sessions, storage)
    finally:
        storage.close()
        sessions.kw["bind"].dispose()


if __name__ == "__main__":
    main()
