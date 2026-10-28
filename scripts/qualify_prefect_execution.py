"""Local process-worker qualification; only fixed loopback services and *_test state."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import boto3
import httpx
from botocore.config import Config
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import WorkPoolCreate
from prefect.workers.process import ProcessWorker
from sqlalchemy import select
from sqlalchemy.engine import make_url

from verdant.access_tokens import AccessTokenSettings, AccessTokenVerifier
from verdant.application import RegistryService
from verdant.contracts import SampleCreate
from verdant.flows import verify_artifact
from verdant.identity import IdentityService
from verdant.operations import OperationRequest, OperationService
from verdant.persistence import NamespaceRow, OperationEventRow, make_sessions
from verdant.prefect_submission import PrefectSubmission

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = Path("/tmp/verdant-issue19-execution-receipt.json")
POOL = "verdant-issue19"


def configure():
    url = os.environ["VERDANT_TEST_DATABASE_URL"]
    if not (make_url(url).database or "").endswith("_test"):
        raise RuntimeError("Use an isolated *_test database")
    if os.environ.get("PREFECT_API_URL") != "http://127.0.0.1:18420/api":
        raise RuntimeError("Qualification only targets loopback Prefect")
    values = dict(
        line.split("=", 1)
        for line in (ROOT / "infrastructure/compose/.local/compose.env").read_text().splitlines()
    )
    fixture = json.loads((ROOT / "tests/fixtures/storage/identities.json").read_text())
    credential = next(item for item in fixture["identities"] if item["name"] == "registry")[
        "credentials"
    ][0]
    os.environ.update(
        VERDANT_DATABASE_URL=url,
        VERDANT_OIDC_ISSUER=values["VERDANT_OIDC_ISSUER"],
        VERDANT_WORKER_CLIENT_ID="verdant-worker",
        VERDANT_WORKER_CLIENT_SECRET=values["WORKER_SECRET"],
        VERDANT_API_AUDIENCE="verdant-api",
        VERDANT_INSECURE_LOCAL_HTTP="1",
        VERDANT_S3_ENDPOINT="http://127.0.0.1:18333",
        VERDANT_S3_BUCKET="verdant-artifacts",
        VERDANT_S3_ACCESS_KEY=credential["accessKey"],
        VERDANT_S3_SECRET_KEY=credential["secretKey"],
        PREFECT_WORKER_WEBSERVER_HOST="127.0.0.1",
        PREFECT_WORKER_WEBSERVER_PORT="18421",
    )
    return url


async def pool():
    async with get_client() as client:
        await client.create_work_pool(
            WorkPoolCreate(
                name=POOL,
                type="process",
                base_job_template=ProcessWorker.get_default_base_job_template(),
            ),
            overwrite=True,
        )


async def submit(sessions, actor, ns, operation, deployment):
    with sessions() as session:
        event_id = session.scalar(
            select(OperationEventRow.id).where(OperationEventRow.operation_id == operation.id)
        )
    OperationService(sessions).receive_workflow_event(actor, ns, event_id)
    async with get_client() as client:
        return await PrefectSubmission(sessions, client, deployment).submit(
            actor, ns, operation.id, 1
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    url = configure()
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
    sessions = make_sessions(url)
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
            issuer, "verdant-api", frozenset({"verdant-worker"}), allow_insecure_http=True
        )
    ).verify(response.json()["access_token"])
    identities = IdentityService(sessions)
    principal = identities.provision(claims["iss"], claims["sub"], kind="service")
    ns = uuid4()
    with sessions.begin() as session:
        session.add(NamespaceRow(id=ns, name="Prefect execution qualification"))
    identities.grant(principal, ns, "write")
    actor = identities.resolve(claims["iss"], claims["sub"], kind="service")
    payload = b"Real Prefect process artifact verification\n"
    digest = hashlib.sha256(payload).hexdigest()
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["VERDANT_S3_ENDPOINT"],
        region_name="us-east-1",
        aws_access_key_id=os.environ["VERDANT_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["VERDANT_S3_SECRET_KEY"],
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    key = "qualification/process/" + uuid4().hex
    s3.put_object(Bucket="verdant-artifacts", Key=key, Body=payload)
    revision = RegistryService(sessions).create_sample(
        actor,
        ns,
        SampleCreate(
            source_key=str(uuid4()),
            modality="text",
            object_key=key,
            sha256=digest,
            size_bytes=len(payload),
        ),
    )
    operation = OperationService(sessions).admit(
        actor,
        ns,
        OperationRequest(
            request_key=str(uuid4()),
            kind="verify_sample_artifact",
            inputs={"sample_id": str(revision.sample_id), "revision_id": str(revision.id)},
        ),
    )
    asyncio.run(pool())
    deployment = verify_artifact.to_deployment(
        name="local-verification", work_pool_name=POOL, job_variables={"working_dir": str(ROOT)}
    ).apply()
    dispatch = asyncio.run(submit(sessions, actor, ns, operation, deployment))
    receipt = {
        "operation_id": str(operation.id),
        "namespace_id": str(ns),
        "principal_id": str(principal),
        "deployment_id": str(deployment),
        "provider_run_id": dispatch.provider_reference,
        "sha256": digest,
        "bytes": len(payload),
    }
    RECEIPT.write_text(json.dumps(receipt) + "\n")
    print(json.dumps(receipt))
    s3.close()
    sessions.kw["bind"].dispose()


if __name__ == "__main__":
    main()
