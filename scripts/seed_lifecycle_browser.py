"""Owned, disposable real-ingestion fixture for the automated browser lifecycle test."""

import hashlib
import json
import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config

from verdant.application import RegistryService
from verdant.contracts import (
    DatasetCreate,
    InlineEvidence,
    InterpretationPins,
    MembershipWrite,
    ReviewPin,
    TransformationPin,
)
from verdant.identity import IdentityService
from verdant.ingestion import IngestionService, UploadDeclaration, UploadRequest
from verdant.ingestion_runner import IngestionRunner
from verdant.persistence import IngestionItemRow, NamespaceRow, make_sessions
from verdant.storage import S3Storage

ISSUER = "http://127.0.0.1:19081/realms/verdant-browser-e2e"


def pins(changed=False):
    annotation = InlineEvidence(
        format="qualification.annotation.v1", payload={"text": "Changed" if changed else "Original"}
    )
    return InterpretationPins(
        annotation_revision=annotation,
        task_schema_revision=InlineEvidence(
            format="qualification.schema.v1",
            payload={"label": "changed" if changed else "original"},
        ),
        review_evidence=ReviewPin(
            annotation_digest=annotation.sha256,
            decision="rejected" if changed else "accepted",
            rationale="Synthetic input review, not quality approval",
        ),
        transformation=TransformationPin(
            payload={
                "code": "text.upper()" if changed else "text.strip()",
                "configuration": {"changed": changed},
                "environment": {"python": "3.12"},
            }
        ),
    )


def main():
    url = os.environ["VERDANT_DATABASE_URL"]
    if (
        url
        != "postgresql+psycopg://postgres:browser-e2e-database@127.0.0.1:19032/verdant_browser_e2e_test"
    ):
        raise RuntimeError("Only the isolated browser test database is allowed")
    output = Path(sys.argv[-1])
    sessions = make_sessions(url)
    identities = IdentityService(sessions)
    if sys.argv[1] == "--revoke-editor":
        fixture = json.loads(output.read_text())
        identities.revoke_grant(UUID(fixture["editor_id"]), UUID(fixture["namespace_id"]))
        return
    command.upgrade(Config("alembic.ini"), "head")
    ns = uuid4()
    with sessions.begin() as session:
        session.add(NamespaceRow(id=ns, name="Automated lifecycle"))
    actors = []
    for subject in ["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"]:
        identity = identities.provision(ISSUER, subject, kind="human")
        identities.grant(identity, ns, "write")
        actors.append(identities.resolve(ISSUER, subject))
    actor = actors[0]
    registry = RegistryService(sessions)
    dataset = registry.create_dataset(
        actor,
        ns,
        DatasetCreate(name="Automated lifecycle", slug="automated-lifecycle", modality="text"),
    )
    storage = S3Storage.from_env()
    service = IngestionService(sessions, bucket=storage.settings.bucket, storage=storage)
    runner = IngestionRunner(sessions, storage)
    members = []
    original = pins()
    for offset, count in [(0, 100), (100, 1)]:
        bodies = [
            f"Synthetic lifecycle sample {offset + index}.\n".encode() for index in range(count)
        ]
        batch = service.admit(
            actor,
            ns,
            UploadRequest(
                request_key=str(uuid4()),
                items=[
                    UploadDeclaration(
                        source_key=f"sample-{offset + index}",
                        modality="text",
                        sha256=hashlib.sha256(body).hexdigest(),
                        size_bytes=len(body),
                        metadata={"label": "cat"},
                    )
                    for index, body in enumerate(bodies)
                ],
            ),
        )
        for item, body in zip(batch.items, bodies, strict=True):
            service.initiate_upload(actor, ns, batch.operation.id, item.id, 1)
            with sessions() as session:
                row = session.get(IngestionItemRow, item.id)
                storage.client.upload_part(
                    Bucket=storage.settings.bucket,
                    Key=row.staging_key,
                    UploadId=row.upload_id,
                    PartNumber=1,
                    Body=body,
                )
            service.complete_upload(actor, ns, batch.operation.id, item.id, 1)
        while batch.operation.status != "succeeded":
            batch = runner.run(actor, ns, batch.operation.id, 1)
        for item in batch.items:
            registry.set_membership(
                actor,
                ns,
                dataset.id,
                item.sample_id,
                MembershipWrite(
                    expected_revision=len(members),
                    sample_revision_id=item.accepted_revision_id,
                    split="train",
                    interpretation=original,
                ),
            )
            members.append(
                {
                    "sample_id": str(item.sample_id),
                    "sample_revision_id": str(item.accepted_revision_id),
                    "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                }
            )
    members.sort(key=lambda item: item["sample_id"])
    output.write_text(
        json.dumps(
            {
                "namespace_id": str(ns),
                "dataset_id": str(dataset.id),
                "owner_id": str(actors[0].id),
                "editor_id": str(actors[1].id),
                "members": members,
                "original_pins": original.model_dump(mode="json", by_alias=True),
                "changed_pins": pins(True).model_dump(mode="json", by_alias=True),
            },
            indent=2,
        )
        + "\n"
    )
    storage.close()


if __name__ == "__main__":
    main()
