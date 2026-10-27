"""Bounded local Compose smoke. Never prints credentials, bearer tokens or signed URLs."""

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from uuid import UUID

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "infrastructure/compose/.local"
ISSUER = "http://auth.127.0.0.1.sslip.io:18801/realms/verdant-local"


def wait_for_registry(client):
    deadline = time.monotonic() + 45
    while True:
        try:
            response = client.get("http://127.0.0.1:18800/health/ready", timeout=3)
            if response.status_code == 200:
                return response
            if response.status_code not in {502, 503, 504}:
                response.raise_for_status()
        except httpx.TransportError:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("Registry readiness unavailable after 45 seconds")
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-only", action="store_true", help="Never recreate the probe object"
    )
    args = parser.parse_args()
    values = dict(line.split("=", 1) for line in (LOCAL / "compose.env").read_text().splitlines())
    with httpx.Client(timeout=10, trust_env=False) as client:
        host = client.get(ISSUER + "/.well-known/openid-configuration")
        host.raise_for_status()
        assert host.json()["issuer"] == ISSUER
        ready = wait_for_registry(client)
        ready.raise_for_status()
        assert ready.json()["schema_revision"] == "0005_identity_audit"
        token = client.post(
            ISSUER + "/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "verdant-worker",
                "client_secret": values["WORKER_SECRET"],
            },
        )
        token.raise_for_status()
        bearer = token.json()["access_token"]
        # Decode only to locate the operator binding. API performs cryptographic verification.
        import base64

        claims = json.loads(base64.urlsafe_b64decode(bearer.split(".")[1] + "=="))
        assert claims["iss"] == ISSUER
        principal_path = LOCAL / "service-receipt.json"
        if args.verify_only:
            principal = json.loads(principal_path.read_text())["principal_id"]
        else:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    "verdant-platform-api-1",
                    "python",
                    "-m",
                    "verdant.operator",
                    "provision",
                    ISSUER,
                    claims["sub"],
                    "--kind",
                    "service",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            principal = json.loads(result.stdout)["principal_id"]
            principal_path.write_text(json.dumps({"principal_id": principal}) + "\n")
        UUID(principal)
        response = client.get(
            "http://127.0.0.1:18800/api/v1/namespaces/00000000-0000-0000-0000-000000000012/datasets/00000000-0000-0000-0000-000000000012",
            headers={"Authorization": "Bearer " + bearer},
        )
        assert response.status_code == 403, response.status_code
        namespace = "00000000-0000-0000-0000-000000000013"
        # Explicit local operator setup, separate from the namespace used for denial.
        setup = """import os
from uuid import UUID
from verdant.persistence import NamespaceRow, make_sessions
sessions = make_sessions(os.environ['VERDANT_DATABASE_URL'])
with sessions.begin() as session:
    key = UUID('00000000-0000-0000-0000-000000000013')
    if session.get(NamespaceRow, key) is None:
        session.add(NamespaceRow(id=key, name='Compose qualification'))
"""
        if not args.verify_only:
            subprocess.run(
                ["docker", "exec", "verdant-platform-api-1", "python", "-c", setup], check=True
            )
            subprocess.run(
                [
                    "docker",
                    "exec",
                    "verdant-platform-api-1",
                    "python",
                    "-m",
                    "verdant.operator",
                    "grant",
                    principal,
                    namespace,
                    "write",
                ],
                check=True,
            )
        receipt_path = LOCAL / "registry-receipt.json"
        headers = {"Authorization": "Bearer " + bearer}
        base = f"http://127.0.0.1:18800/api/v1/namespaces/{namespace}/datasets"
        if not receipt_path.exists():
            if args.verify_only:
                raise RuntimeError("Missing registry receipt; run initial smoke first")
            created = client.post(
                base,
                headers=headers,
                json={
                    "slug": "compose-qualification",
                    "name": "Compose qualification",
                    "modality": "image",
                },
            )
            created.raise_for_status()
            receipt_path.write_text(json.dumps(created.json()) + "\n")
        dataset = json.loads(receipt_path.read_text())
        read = client.get(base + "/" + dataset["id"], headers=headers)
        read.raise_for_status()
        assert read.json() == dataset
        assert dataset["owner_id"] == principal
    storage = boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:18833",
        region_name="us-east-1",
        aws_access_key_id="verdant-local-registry",
        aws_secret_access_key=values["OBJECT_REGISTRY_SECRET"],
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    bucket, key = "verdant-artifacts", "qualification/compose-persistence-v1"
    payload = b"Verdant Compose durable object qualification v1\n"
    digest = hashlib.sha256(payload).hexdigest()
    if not args.verify_only:
        try:
            storage.put_object(
                Bucket=bucket, Key=key, Body=payload, Metadata={"sha256": digest}, IfNoneMatch="*"
            )
        except ClientError as exc:
            if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
                raise
    found = storage.get_object(Bucket=bucket, Key=key)
    assert found["Body"].read() == payload
    assert found["Metadata"]["sha256"] == digest
    signed = storage.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=30
    )
    with httpx.Client(trust_env=False, timeout=5) as client:
        assert client.get(signed).content == payload
        assert client.get(f"http://127.0.0.1:18833/{bucket}/{key}").status_code == 403
    print(
        json.dumps(
            {
                "schema": ready.json()["schema_revision"],
                "issuer": ISSUER,
                "service_principal": principal,
                "no_grant_status": response.status_code,
                "object_sha256": digest,
                "presigned_read": "passed",
                "anonymous": "denied",
            }
        )
    )


if __name__ == "__main__":
    main()
