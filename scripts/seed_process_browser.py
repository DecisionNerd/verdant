"""Owned process fixture: real PostgreSQL/S3 and independently verified Keycloak identities."""

import hashlib
import io
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import httpx
from alembic import command
from alembic.config import Config
from sqlalchemy import func

from verdant.access_tokens import AccessTokenSettings, AccessTokenVerifier
from verdant.application import RegistryService
from verdant.contracts import DatasetCreate, MembershipWrite, SampleCreate
from verdant.identity import IdentityService
from verdant.persistence import (
    ArtifactLocationRow,
    ArtifactRow,
    NamespaceRow,
    SampleArtifactRow,
    make_sessions,
)
from verdant.storage import ObjectDigest, S3Storage
from verdant.versioning import CandidateCapture, CandidatePrepare, VersioningService


def main():
    url = os.environ["VERDANT_DATABASE_URL"]
    if (
        url
        != "postgresql+psycopg://postgres:process-e2e-database@127.0.0.1:19132/verdant_process_test"
    ):
        raise RuntimeError("Only the isolated 191xx process test database is allowed")
    command.upgrade(Config("alembic.ini"), "head")
    sessions = make_sessions(url)
    identity = IdentityService(sessions)
    namespace = uuid4()
    issuer = os.environ["VERDANT_OIDC_ISSUER"]
    with sessions.begin() as session:
        session.add(NamespaceRow(id=namespace, name="Process qualification"))
    owner = identity.provision(issuer, "00000000-0000-0000-0000-000000000001", kind="human")
    identity.grant(owner, namespace, "write")
    actor = identity.resolve(issuer, "00000000-0000-0000-0000-000000000001")
    with httpx.Client(timeout=5, trust_env=False) as client:
        response = client.post(
            issuer + "/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "verdant-worker",
                "client_secret": os.environ["VERDANT_WORKER_CLIENT_SECRET"],
                "scope": "verdant",
            },
        )
        response.raise_for_status()
    claims = AccessTokenVerifier(
        AccessTokenSettings(
            issuer, "verdant-api", frozenset({"verdant-worker"}), allow_insecure_http=True
        )
    ).verify(response.json()["access_token"])
    worker = identity.provision(claims["iss"], claims["sub"], kind="service")
    identity.grant(worker, namespace, "write")
    registry = RegistryService(sessions)
    dataset = registry.create_dataset(
        actor,
        namespace,
        DatasetCreate(name="Process qualification", slug="process-qualification", modality="text"),
    )
    storage = S3Storage.from_env()
    members = []
    for index in range(3):
        body = f"  Safe process text {index}  ".encode()

        expected = ObjectDigest(hashlib.sha256(body).hexdigest(), len(body))
        artifact_id = uuid4()
        key = f"{namespace}/artifacts/{artifact_id}"
        storage.write_immutable(key, io.BytesIO(body), expected)
        storage.verify(key, expected)
        revision = registry.create_sample(
            actor,
            namespace,
            SampleCreate(
                source_key=f"process-{index}",
                modality="text",
                object_key=key,
                sha256=expected.sha256,
                size_bytes=len(body),
                metadata={"truth_label": "HELD-OUT-SECRET", "answer": "must never enter calls"},
            ),
        )
        with sessions.begin() as session:
            session.add(
                ArtifactRow(
                    id=artifact_id,
                    namespace_id=namespace,
                    sha256=expected.sha256,
                    size_bytes=len(body),
                    verified_at=func.now(),
                )
            )
            session.flush()
            session.add(
                ArtifactLocationRow(
                    namespace_id=namespace,
                    artifact_id=artifact_id,
                    store_id="primary",
                    bucket=storage.settings.bucket,
                    object_key=key,
                )
            )
            session.add(
                SampleArtifactRow(
                    namespace_id=namespace,
                    sample_id=revision.sample_id,
                    revision_id=revision.id,
                    artifact_id=artifact_id,
                )
            )
        registry.set_membership(
            actor,
            namespace,
            dataset.id,
            revision.sample_id,
            MembershipWrite(expected_revision=index, sample_revision_id=revision.id, split="test"),
        )
        members.append({"sample_id": str(revision.sample_id), "revision_id": str(revision.id)})
    versions = VersioningService(sessions)
    candidate = versions.capture(
        actor,
        namespace,
        dataset.id,
        CandidateCapture(request_key=str(uuid4()), expected_revision=3),
    )
    versions.prepare(
        actor, namespace, dataset.id, candidate.id, CandidatePrepare(expected_count=0, limit=25)
    )
    Path(sys.argv[1]).write_text(
        json.dumps(
            {
                "namespace_id": str(namespace),
                "dataset_id": str(dataset.id),
                "candidate_id": str(candidate.id),
                "actor_id": str(actor.id),
                "worker_id": str(worker),
                "members": members,
            },
            indent=2,
        )
    )
    storage.close()
    sessions.kw["bind"].dispose()


if __name__ == "__main__":
    main()
