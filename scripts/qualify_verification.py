"""Run against only an isolated *_test database and the loopback SeaweedFS fixture."""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.config import Config
from sqlalchemy.engine import make_url

from verdant.application import RegistryService
from verdant.contracts import SampleCreate
from verdant.identity import IdentityService
from verdant.operations import OperationRequest, OperationService
from verdant.persistence import NamespaceRow, make_sessions
from verdant.verification import VerificationFailed, VerificationRunner

url = os.environ["VERDANT_TEST_DATABASE_URL"]
if not (make_url(url).database or "").endswith("_test"):
    raise SystemExit("Qualification requires an isolated database ending _test")
credentials = json.loads(
    (Path(__file__).parents[1] / "tests/fixtures/storage/identities.json").read_text()
)
credential = next(item for item in credentials["identities"] if item["name"] == "registry")[
    "credentials"
][0]
s3 = boto3.client(
    "s3",
    endpoint_url="http://127.0.0.1:18333",
    region_name="us-east-1",
    aws_access_key_id=credential["accessKey"],
    aws_secret_access_key=credential["secretKey"],
    config=Config(
        signature_version="s3v4",
        s3={"addressing_style": "path"},
        connect_timeout=3,
        read_timeout=5,
        retries={"max_attempts": 2},
    ),
)
sessions = make_sessions(url)
identities = IdentityService(sessions)
subject, namespace = str(uuid4()), uuid4()
principal_id = identities.provision("https://local-qualification.invalid", subject, kind="service")
with sessions.begin() as session:
    session.add(NamespaceRow(id=namespace, name="Real artifact verification qualification"))
identities.grant(principal_id, namespace, "write")
actor = identities.resolve("https://local-qualification.invalid", subject, kind="service")
bucket, key = "verdant-artifacts", "qualification/operations/" + uuid4().hex
payload = b"Durable artifact verification qualification\n"
digest = hashlib.sha256(payload).hexdigest()
# Only this newly generated synthetic fixture is corrupted and repaired.
s3.put_object(Bucket=bucket, Key=key, Body=b"corrupted fixture")
revision = RegistryService(sessions).create_sample(
    actor,
    namespace,
    SampleCreate(
        source_key=str(uuid4()),
        modality="text",
        object_key=key,
        sha256=digest,
        size_bytes=len(payload),
    ),
)
operations = OperationService(sessions)
operation = operations.admit(
    actor,
    namespace,
    OperationRequest(
        request_key=str(uuid4()),
        kind="verify_sample_artifact",
        inputs={"sample_id": str(revision.sample_id), "revision_id": str(revision.id)},
    ),
)
runner = VerificationRunner(sessions, s3, bucket)
try:
    runner.run(actor, namespace, operation.id, 1)
except VerificationFailed:
    assert operations.get(actor, namespace, operation.id).status == "failed"
else:
    raise RuntimeError("Corrupt artifact incorrectly succeeded")
s3.put_object(Bucket=bucket, Key=key, Body=payload)
operations.retry(actor, namespace, operation.id, 1)
finished = runner.run(actor, namespace, operation.id, 2)
assert finished.status == "succeeded"
assert runner.run(actor, namespace, operation.id, 2) == finished
print(
    json.dumps(
        {
            "operation_id": str(operation.id),
            "namespace_id": str(namespace),
            "epoch": finished.epoch,
            "status": finished.status,
            "sha256": digest,
            "bytes": len(payload),
            "corruption": "denied",
            "retry": "read reconciled",
            "repeat": "receipt reused",
        }
    )
)
sessions.kw["bind"].dispose()
