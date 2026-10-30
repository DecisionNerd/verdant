"""Preserve actual schema14 writer data across the combined migration18 upgrade.

Prepare with the owned #15 source on PYTHONPATH; verify with the integrated #14 source.
Only the fixed isolated test database is eligible. No browser, S3 or real SSO claim.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import MetaData, Table, select, text

from verdant.application import RegistryService
from verdant.contracts import (
    SCHEMA_REVISION,
    DatasetCreate,
    InlineEvidence,
    InterpretationPins,
    MembershipWrite,
    SampleCreate,
)
from verdant.domain import Conflict
from verdant.identity import IdentityService
from verdant.persistence import Base, NamespaceRow, make_sessions
from verdant.versioning import (
    CandidateCapture,
    CandidatePrepare,
    CandidateSelection,
    VersioningService,
)

RECEIPT = Path("/tmp/verdant-issue14-upgrade-receipt.json")


def snapshot(sessions, names):
    result = {}
    with sessions() as session:
        for name in sorted(names):
            table = Table(name, MetaData(), autoload_with=session.connection())
            rows = [
                dict(row)
                for row in session.execute(
                    select(table).order_by(*table.primary_key.columns)
                ).mappings()
            ]
            payload = json.dumps(rows, sort_keys=True, default=str, separators=(",", ":"))
            result[name] = {
                "count": len(rows),
                "sha256": hashlib.sha256(payload.encode()).hexdigest(),
            }
    return result


def prepare(sessions):
    if SCHEMA_REVISION != "0014_interpretation" or RECEIPT.exists():
        raise RuntimeError("Use schema14 reader/writer source and a fresh receipt")
    with sessions() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA_REVISION
    identities = IdentityService(sessions)
    subject = str(uuid4())
    principal = identities.provision("https://upgrade-proof.invalid", subject, kind="human")
    ns = uuid4()
    with sessions.begin() as session:
        session.add(NamespaceRow(id=ns, name="Schema14 preservation fixture"))
    identities.grant(principal, ns, "write")
    actor = identities.resolve("https://upgrade-proof.invalid", subject)
    registry = RegistryService(sessions)
    dataset = registry.create_dataset(
        actor, ns, DatasetCreate(name="Upgrade proof", slug="upgrade", modality="text")
    )
    samples = []
    for index in range(3):
        revision = registry.create_sample(
            actor,
            ns,
            SampleCreate(
                source_key=f"source-{index}",
                modality="text",
                object_key=f"unverified/upgrade/{index}",
                sha256="a" * 64,
                size_bytes=1,
                metadata={"index": index},
            ),
        )
        samples.append(revision)
        pins = InterpretationPins(
            task_schema_revision=InlineEvidence(
                format="upgrade.schema.v1", payload={"label": "text"}
            ),
            annotation_revision=InlineEvidence(
                format="upgrade.annotation.v1", payload={"value": index}
            ),
        )
        registry.set_membership(
            actor,
            ns,
            dataset.id,
            revision.sample_id,
            MembershipWrite(
                expected_revision=index,
                sample_revision_id=revision.id,
                split="train",
                interpretation=pins,
            ),
        )
    versioning = VersioningService(sessions)
    partial = versioning.capture(
        actor, ns, dataset.id, CandidateCapture(request_key="partial", expected_revision=3)
    )
    partial = versioning.prepare(
        actor, ns, dataset.id, partial.id, CandidatePrepare(expected_count=0, limit=2)
    )
    assert partial.state == "preparing" and partial.prepared_count == 2
    sealed = versioning.capture(
        actor, ns, dataset.id, CandidateCapture(request_key="sealed", expected_revision=3)
    )
    sealed = versioning.prepare(
        actor, ns, dataset.id, sealed.id, CandidatePrepare(expected_count=0)
    )
    assert sealed.state == "sealed"
    registry.set_membership(
        actor,
        ns,
        dataset.id,
        samples[0].sample_id,
        MembershipWrite(
            expected_revision=3,
            sample_revision_id=samples[0].id,
            split="validation",
        ),
    )
    receipt = {
        "database": "verdant_issue14_upgrade_test",
        "before_schema": SCHEMA_REVISION,
        "namespace_id": str(ns),
        "dataset_id": str(dataset.id),
        "subject": subject,
        "partial_id": str(partial.id),
        "sealed_id": str(sealed.id),
        "partial_before": versioning.get(actor, ns, dataset.id, partial.id).model_dump(mode="json"),
        "sealed_members": versioning.members(actor, ns, dataset.id, sealed.id).model_dump(
            mode="json"
        ),
        "before_tables": snapshot(sessions, Base.metadata.tables),
    }
    RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n")
    print(
        json.dumps({"schema": SCHEMA_REVISION, "partial": "2/3", "sealed": 3, "draft_revision": 4})
    )


def verify(sessions):
    assert SCHEMA_REVISION == "0018_ingestion_sources"
    receipt = json.loads(RECEIPT.read_text())
    with sessions() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA_REVISION
    after = snapshot(sessions, receipt["before_tables"])
    assert after == receipt["before_tables"], "Migration changed preexisting row content"
    identities = IdentityService(sessions)
    actor = identities.resolve("https://upgrade-proof.invalid", receipt["subject"])
    ns, dataset = UUID(receipt["namespace_id"]), UUID(receipt["dataset_id"])
    partial, sealed = UUID(receipt["partial_id"]), UUID(receipt["sealed_id"])
    service = VersioningService(sessions)
    assert (
        service.get(actor, ns, dataset, partial).model_dump(mode="json")
        == receipt["partial_before"]
    )
    assert (
        service.members(actor, ns, dataset, sealed).model_dump(mode="json")
        == receipt["sealed_members"]
    )
    resumed = service.prepare(
        actor, ns, dataset, partial, CandidatePrepare(expected_count=2, limit=100)
    )
    assert (
        resumed.state == "sealed" and resumed.prepared_count == 3 and resumed.source_revision == 3
    )
    assert (
        service.members(actor, ns, dataset, partial).model_dump(mode="json")["items"]
        == receipt["sealed_members"]["items"]
    )
    try:
        service.check_selection(
            actor, ns, dataset, partial, CandidateSelection(mode="current_draft")
        )
    except Conflict:
        pass
    else:
        raise AssertionError("Current-draft selection must reject the old captured revision")
    service.check_selection(
        actor, ns, dataset, partial, CandidateSelection(mode="selected_candidate")
    )
    receipt.update(
        after_schema=SCHEMA_REVISION,
        old_table_content_preserved=True,
        partial_resumed_to_sealed=True,
        sealed_members_unchanged=True,
        current_draft_conflict=True,
        explicit_selection_accepted=True,
    )
    RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n")
    print(
        json.dumps(
            {
                "all_schema14_row_hashes_preserved": True,
                "resumed_count": 3,
                "source_revision": 3,
                "latest_draft_revision": 4,
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare14", action="store_true")
    args = parser.parse_args()
    url = os.environ["VERDANT_DATABASE_URL"]
    if not url.endswith("/verdant_issue14_upgrade_test"):
        raise RuntimeError("Only the explicit isolated upgrade proof database is eligible")
    sessions = make_sessions(url)
    try:
        prepare(sessions) if args.prepare14 else verify(sessions)
    finally:
        sessions.kw["bind"].dispose()


if __name__ == "__main__":
    main()
