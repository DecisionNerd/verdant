import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event, func, select

from verdant.application import RegistryService
from verdant.contracts import SampleRevise
from verdant.domain import Conflict, Forbidden
from verdant.identity import IdentityService
from verdant.ingestion import IngestionService, UploadDeclaration, UploadRequest
from verdant.ingestion_runner import IngestionFailed, IngestionRunner
from verdant.persistence import (
    ArtifactRow,
    AuditRow,
    EffectReceiptRow,
    IngestionItemRow,
    SampleArtifactRow,
    SampleRevisionRow,
    SampleRow,
)
from verdant.storage import ObjectDigest, StorageUnavailable


@pytest.fixture
def ready_ingestion(registry, sessions, storage):
    _, actor, ns = registry
    adapter, _ = storage
    owned = []

    def create(entries=None, *, target=None, metadata=None):
        entries = entries or [("source", b"verified content")]
        service = IngestionService(sessions, bucket=adapter.settings.bucket, storage=adapter)
        batch = service.admit(
            actor,
            ns,
            UploadRequest(
                request_key=str(uuid4()),
                items=[
                    UploadDeclaration(
                        source_key=name,
                        modality="text",
                        sha256=hashlib.sha256(body).hexdigest(),
                        size_bytes=len(body),
                        metadata=metadata or {},
                        target_sample_id=target[0] if target else None,
                        expected_revision=target[1] if target else None,
                    )
                    for name, body in entries
                ],
            ),
        )
        for item, (_, body) in zip(batch.items, entries, strict=True):
            service.initiate_upload(actor, ns, batch.operation.id, item.id, 1)
            with sessions() as session:
                stored = session.get(IngestionItemRow, item.id)
                owned.append((stored.staging_key, stored.upload_id))
            signed = service.sign_upload_part(actor, ns, batch.operation.id, item.id, 1, 1)
            with httpx.Client(timeout=10, trust_env=False) as http:
                http.put(signed.url, content=body).raise_for_status()
            batch = service.complete_upload(actor, ns, batch.operation.id, item.id, 1)
        return service, batch

    yield create, actor, ns, adapter
    # Retain accepted final bytes with their persistent test-database references.
    for key, upload_id in owned:
        adapter.abort(key, upload_id)
        adapter.delete_one(key)


def count(session, model, ns):
    return session.scalar(select(func.count()).select_from(model).where(model.namespace_id == ns))


def test_verified_acceptance_and_replacement_preserve_sample_and_history(ready_ingestion, sessions):
    create, actor, ns, adapter = ready_ingestion
    service, batch = create(metadata={"title": "original"})
    runner = IngestionRunner(sessions, adapter)
    accepted = runner.run(actor, ns, batch.operation.id, 1)
    assert accepted.operation.status == "succeeded" and accepted.items[0].state == "accepted"
    assert runner.run(actor, ns, batch.operation.id, 1) == accepted
    first = accepted.items[0]
    registry = RegistryService(sessions)
    old = registry.get_sample_revision(actor, ns, first.sample_id, first.accepted_revision_id)
    assert old.revision == 1 and old.metadata == {"title": "original"}
    with sessions() as session:
        assert session.get(ArtifactRow, first.artifact_id).verified_at is not None
        assert session.get(SampleArtifactRow, old.id).artifact_id == first.artifact_id
    _, replacement = create(
        [("source", b"replacement bytes")],
        target=(first.sample_id, 1),
        metadata={"title": "changed"},
    )
    revised = runner.run(actor, ns, replacement.operation.id, 1).items[0]
    assert revised.sample_id == first.sample_id and revised.artifact_id != first.artifact_id
    new = registry.get_sample_revision(actor, ns, revised.sample_id, revised.accepted_revision_id)
    assert new.revision == 2 and new.metadata == {"title": "changed"}
    assert registry.get_sample_revision(actor, ns, first.sample_id, old.id) == old
    adapter.verify(old.object_key, ObjectDigest(old.sha256, old.size_bytes))
    adapter.verify(new.object_key, ObjectDigest(new.sha256, new.size_bytes))


def test_recovery_after_promotion_before_acceptance_reuses_final_bytes(
    ready_ingestion, sessions, monkeypatch
):
    create, actor, ns, adapter = ready_ingestion
    _, batch = create()
    runner = IngestionRunner(sessions, adapter)
    original = runner.ingestion.accept_verified_item

    def crash(*args):
        raise RuntimeError("injected interruption before database acceptance")

    monkeypatch.setattr(runner.ingestion, "accept_verified_item", crash)
    with pytest.raises(RuntimeError, match="injected interruption"):
        runner.run(actor, ns, batch.operation.id, 1)
    with sessions() as session:
        item = session.get(IngestionItemRow, batch.items[0].id)
        assert item.state == "ready" and count(session, SampleRow, ns) == 0
        assert session.get(ArtifactRow, item.artifact_id).verified_at is None
        receipt = session.scalar(
            select(EffectReceiptRow).where(
                EffectReceiptRow.operation_id == batch.operation.id,
                EffectReceiptRow.identity["kind"].as_string() == "artifact.promote",
            )
        )
        assert receipt.status == "unknown"
        staging = item.staging_key
    adapter.delete_one(staging)
    monkeypatch.setattr(runner.ingestion, "accept_verified_item", original)
    assert runner.run(actor, ns, batch.operation.id, 1).operation.status == "succeeded"
    with sessions() as session:
        assert count(session, SampleRow, ns) == count(session, SampleRevisionRow, ns) == 1


def test_audit_failure_rolls_back_effect_result_and_sample_acceptance(ready_ingestion, sessions):
    create, actor, ns, adapter = ready_ingestion
    _, batch = create()
    runner = IngestionRunner(sessions, adapter)

    def fail(mapper, connection, row):
        if row.namespace_id == ns and row.action == "sample.created":
            raise RuntimeError("sample audit failure")

    event.listen(AuditRow, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="sample audit failure"):
            runner.run(actor, ns, batch.operation.id, 1)
    finally:
        event.remove(AuditRow, "before_insert", fail)
    with sessions() as session:
        assert count(session, SampleRow, ns) == count(session, SampleArtifactRow, ns) == 0
        item = session.get(IngestionItemRow, batch.items[0].id)
        assert (
            item.state == "ready" and session.get(ArtifactRow, item.artifact_id).verified_at is None
        )
        receipt = session.scalar(
            select(EffectReceiptRow).where(
                EffectReceiptRow.operation_id == batch.operation.id,
                EffectReceiptRow.identity["kind"].as_string() == "artifact.promote",
            )
        )
        assert receipt.status == "unknown" and receipt.result_digest is None
    assert runner.run(actor, ns, batch.operation.id, 1).operation.status == "succeeded"


def test_failed_item_retry_preserves_accepted_items_and_stable_effects(ready_ingestion, sessions):
    create, actor, ns, adapter = ready_ingestion
    _, batch = create([("first", b"first"), ("second", b"second")])
    runner = IngestionRunner(sessions, adapter)
    partial = runner.run(actor, ns, batch.operation.id, 1, limit=1)
    assert partial.operation.status == "running" and partial.items[0].state == "accepted"
    with sessions() as session:
        staged = session.get(IngestionItemRow, batch.items[1].id).staging_key
    adapter.client.put_object(Bucket=adapter.settings.bucket, Key=staged, Body=b"BROKEN")
    with pytest.raises(IngestionFailed, match="artifact_corrupt"):
        runner.run(actor, ns, batch.operation.id, 1)
    failed = runner.ingestion.get(actor, ns, batch.operation.id)
    assert failed.operation.status == "failed" and failed.items[1].error == "artifact_corrupt"
    assert failed.items[0] == partial.items[0]
    adapter.client.put_object(Bucket=adapter.settings.bucket, Key=staged, Body=b"second")
    runner.operations.retry(actor, ns, batch.operation.id, 1)
    done = runner.run(actor, ns, batch.operation.id, 2)
    assert done.operation.status == "succeeded" and done.items[0] == partial.items[0]
    with sessions() as session:
        assert count(session, SampleRow, ns) == count(session, SampleRevisionRow, ns) == 2
        receipts = session.scalars(
            select(EffectReceiptRow).where(
                EffectReceiptRow.operation_id == batch.operation.id,
                EffectReceiptRow.identity["kind"].as_string() == "artifact.promote",
            )
        ).all()
        assert len(receipts) == 2 and all(r.status == "succeeded" for r in receipts)


def test_replacement_rechecks_revision_at_acceptance(ready_ingestion, sessions):
    create, actor, ns, adapter = ready_ingestion
    _, initial = create()
    runner = IngestionRunner(sessions, adapter)
    first = runner.run(actor, ns, initial.operation.id, 1).items[0]
    _, replacement = create([("source", b"new")], target=(first.sample_id, 1))
    registry = RegistryService(sessions)
    old = registry.get_sample_revision(actor, ns, first.sample_id, first.accepted_revision_id)
    registry.revise_sample(
        actor,
        ns,
        first.sample_id,
        SampleRevise(
            expected_revision=1,
            sha256=old.sha256,
            size_bytes=old.size_bytes,
            object_key=old.object_key,
            metadata={"concurrent": True},
        ),
    )
    with pytest.raises(Conflict, match="revision changed before acceptance"):
        runner.run(actor, ns, replacement.operation.id, 1)
    with sessions() as session:
        assert count(session, SampleRevisionRow, ns) == 2
        artifact = session.get(ArtifactRow, replacement.items[0].artifact_id)
        assert artifact.verified_at is None
    assert runner.ingestion.get(actor, ns, replacement.operation.id).operation.status == "failed"


def test_cancellation_after_promotion_prevents_acceptance(ready_ingestion, sessions, monkeypatch):
    create, actor, ns, adapter = ready_ingestion
    _, batch = create()
    runner = IngestionRunner(sessions, adapter)
    original = adapter.promote

    def cancel_after_write(*args):
        result = original(*args)
        runner.operations.request_cancel(actor, ns, batch.operation.id, 1)
        return result

    monkeypatch.setattr(adapter, "promote", cancel_after_write)
    with pytest.raises(Conflict):
        runner.run(actor, ns, batch.operation.id, 1)
    state = runner.ingestion.get(actor, ns, batch.operation.id)
    assert state.operation.status == "cancellation_requested" and state.items[0].state == "ready"
    with sessions() as session:
        assert count(session, SampleRow, ns) == 0
        assert session.get(ArtifactRow, batch.items[0].artifact_id).verified_at is None


def test_duplicate_workers_accept_one_revision(ready_ingestion, sessions, monkeypatch):
    create, actor, ns, adapter = ready_ingestion
    _, batch = create()
    barrier = Barrier(2)
    original = adapter.promote

    def overlap(*args):
        barrier.wait(timeout=10)
        return original(*args)

    monkeypatch.setattr(adapter, "promote", overlap)

    def run(_):
        return IngestionRunner(sessions, adapter).run(actor, ns, batch.operation.id, 1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert results[0] == results[1] and results[0].operation.status == "succeeded"
    with sessions() as session:
        assert count(session, SampleRow, ns) == count(session, SampleRevisionRow, ns) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(EffectReceiptRow)
                .where(
                    EffectReceiptRow.operation_id == batch.operation.id,
                    EffectReceiptRow.identity["kind"].as_string() == "artifact.promote",
                )
            )
            == 1
        )


def test_competing_operations_cannot_create_same_source_twice(
    ready_ingestion, sessions, monkeypatch
):
    create, actor, ns, adapter = ready_ingestion
    _, first = create()
    _, second = create()
    barrier = Barrier(2)
    original = adapter.promote

    def overlap(*args):
        result = original(*args)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(adapter, "promote", overlap)

    def run(batch):
        try:
            IngestionRunner(sessions, adapter).run(actor, ns, batch.operation.id, 1)
            return "succeeded"
        except Conflict as exc:
            assert "Source identity now exists" in str(exc)
            return "failed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, [first, second])) == ["failed", "succeeded"]
    with sessions() as session:
        assert count(session, SampleRow, ns) == count(session, SampleRevisionRow, ns) == 1


def test_lost_final_write_response_retries_across_epoch_without_staging(
    ready_ingestion, sessions, monkeypatch
):
    create, actor, ns, adapter = ready_ingestion
    _, batch = create()
    runner = IngestionRunner(sessions, adapter)
    original = adapter.promote

    def lose_response(*args):
        original(*args)
        raise StorageUnavailable("Final promotion response lost")

    monkeypatch.setattr(adapter, "promote", lose_response)
    with pytest.raises(IngestionFailed, match="storage_unavailable"):
        runner.run(actor, ns, batch.operation.id, 1)
    with sessions() as session:
        staging = session.get(IngestionItemRow, batch.items[0].id).staging_key
    adapter.delete_one(staging)
    monkeypatch.setattr(adapter, "promote", original)
    runner.operations.retry(actor, ns, batch.operation.id, 1)
    assert runner.run(actor, ns, batch.operation.id, 2).operation.status == "succeeded"
    with sessions() as session:
        assert count(session, SampleRevisionRow, ns) == 1


def test_unrelated_receipt_cannot_authorize_acceptance(ready_ingestion, sessions):
    create, actor, ns, adapter = ready_ingestion
    service, batch = create()
    service.operations.start(actor, ns, batch.operation.id, 1)
    with sessions() as session:
        receipt = session.scalar(
            select(EffectReceiptRow.id).where(
                EffectReceiptRow.operation_id == batch.operation.id,
                EffectReceiptRow.identity["kind"].as_string() == "artifact.multipart.initiate",
            )
        )
    with pytest.raises(Conflict, match="exact artifact promotion receipt"):
        service.accept_verified_item(actor, ns, batch.operation.id, batch.items[0].id, 1, receipt)
    with sessions() as session:
        assert count(session, SampleRevisionRow, ns) == 0
        assert session.get(ArtifactRow, batch.items[0].artifact_id).verified_at is None


def test_metadata_only_revision_reuses_only_its_existing_verified_binding(
    ready_ingestion, sessions
):
    create, actor, ns, adapter = ready_ingestion
    _, batch = create()
    first = IngestionRunner(sessions, adapter).run(actor, ns, batch.operation.id, 1).items[0]
    registry = RegistryService(sessions)
    original = registry.get_sample_revision(actor, ns, first.sample_id, first.accepted_revision_id)
    revised = registry.revise_sample(
        actor,
        ns,
        first.sample_id,
        SampleRevise(
            expected_revision=1,
            sha256=original.sha256,
            size_bytes=original.size_bytes,
            object_key=original.object_key,
            metadata={"title": "metadata only"},
        ),
    )
    with sessions() as session:
        assert session.get(SampleArtifactRow, revised.id).artifact_id == first.artifact_id
        assert session.get(SampleArtifactRow, original.id).artifact_id == first.artifact_id
    assert revised.revision == 2 and revised.metadata == {"title": "metadata only"}
    changed_key = registry.revise_sample(
        actor,
        ns,
        first.sample_id,
        SampleRevise(
            expected_revision=2,
            sha256=original.sha256,
            size_bytes=original.size_bytes,
            object_key="unverified-other-location",
            metadata={},
        ),
    )
    with sessions() as session:
        assert session.get(SampleArtifactRow, changed_key.id) is None
    from verdant.contracts import SampleCreate

    legacy = registry.create_sample(
        actor,
        ns,
        SampleCreate(
            source_key="legacy",
            modality="text",
            sha256=original.sha256,
            size_bytes=original.size_bytes,
            object_key=original.object_key,
        ),
    )
    legacy_revision = registry.revise_sample(
        actor,
        ns,
        legacy.sample_id,
        SampleRevise(
            expected_revision=1,
            sha256=original.sha256,
            size_bytes=original.size_bytes,
            object_key=original.object_key,
            metadata={"title": "still unverified"},
        ),
    )
    with sessions() as session:
        assert session.get(SampleArtifactRow, legacy_revision.id) is None


def test_revocation_after_promotion_blocks_atomic_acceptance(
    ready_ingestion, sessions, monkeypatch
):
    create, actor, ns, adapter = ready_ingestion
    _, batch = create()
    runner = IngestionRunner(sessions, adapter)
    original = adapter.promote

    def revoke_after_write(*args):
        result = original(*args)
        IdentityService(sessions).revoke_grant(actor.id, ns)
        return result

    monkeypatch.setattr(adapter, "promote", revoke_after_write)
    with pytest.raises(Forbidden):
        runner.run(actor, ns, batch.operation.id, 1)
    with sessions() as session:
        assert count(session, SampleRevisionRow, ns) == 0
        assert session.get(ArtifactRow, batch.items[0].artifact_id).verified_at is None
    IdentityService(sessions).grant(actor.id, ns, "write")
    monkeypatch.setattr(adapter, "promote", original)
    assert runner.run(actor, ns, batch.operation.id, 1).operation.status == "succeeded"
