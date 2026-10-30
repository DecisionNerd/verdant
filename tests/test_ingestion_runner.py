from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import DBAPIError

from verdant.application import RegistryService
from verdant.contracts import SampleRevise
from verdant.domain import Conflict, Forbidden
from verdant.identity import IdentityService
from verdant.ingestion import UploadDeclaration, UploadRequest
from verdant.ingestion_runner import IngestionFailed, IngestionRunner
from verdant.persistence import (
    ArtifactRow,
    AuditRow,
    EffectReceiptRow,
    IngestionItemRow,
    OperationRow,
    SampleArtifactRow,
    SampleRevisionRow,
    SampleRow,
)
from verdant.storage import ObjectDigest, StorageUnavailable


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


def test_reupload_recovers_only_failed_item_through_normal_signed_uploads(
    ready_ingestion, sessions
):
    create, actor, ns, adapter = ready_ingestion
    service, batch = create(
        [("first", b"first"), ("second", b"second")], uploaded_bodies={"second": b"BROKEN"}
    )
    runner = IngestionRunner(sessions, adapter)
    with pytest.raises(IngestionFailed, match="artifact_corrupt"):
        runner.run(actor, ns, batch.operation.id, 1)
    failed = service.get(actor, ns, batch.operation.id)
    accepted, origin = failed.items
    assert accepted.state == "accepted" and origin.error == "artifact_corrupt"
    recovery_service, recovery = create([("second", b"second")], retry_of=origin.id)
    child = recovery.items[0]
    assert child.retry_of_item_id == origin.id and child.artifact_id != origin.artifact_id
    superseded = service.get(actor, ns, batch.operation.id)
    assert superseded.items[0] == accepted
    assert superseded.items[1].state == "superseded"
    assert superseded.items[1].error == "artifact_corrupt"
    with sessions() as session:
        old = session.get(IngestionItemRow, origin.id)
        new = session.get(IngestionItemRow, child.id)
        assert old.staging_key != new.staging_key and old.upload_id != new.upload_id
        op = session.get(OperationRow, recovery.operation.id)
        replay = UploadRequest(request_key=op.request_key, items=op.inputs["items"])
    assert recovery_service.admit(actor, ns, replay) == recovery
    with pytest.raises(Conflict, match="new operation"):
        runner.operations.retry(actor, ns, batch.operation.id, 1)
    done = runner.run(actor, ns, recovery.operation.id, 1)
    assert done.operation.status == "succeeded"
    assert service.get(actor, ns, batch.operation.id).items[0] == accepted
    with sessions() as session:
        assert count(session, SampleRow, ns) == count(session, SampleRevisionRow, ns) == 2
        assert session.get(IngestionItemRow, origin.id).accepted_revision_id is None


def test_reupload_changed_inputs_and_active_or_accepted_origins_are_denied(
    ready_ingestion, sessions
):
    create, actor, ns, adapter = ready_ingestion
    service, active = create()
    with sessions() as session:
        payload = session.get(OperationRow, active.operation.id).inputs["items"][0]
    declaration = UploadDeclaration(**(payload | {"retry_of_item_id": str(active.items[0].id)}))
    with pytest.raises(Conflict, match="failed or cancelled"):
        service.admit(actor, ns, UploadRequest(request_key="active-retry", items=[declaration]))
    service.operations.fail(actor, ns, active.operation.id, 1)
    changed = declaration.model_copy(update={"metadata": {"changed": True}})
    with pytest.raises(Conflict, match="preserve the declaration"):
        service.admit(actor, ns, UploadRequest(request_key="changed-retry", items=[changed]))
    runner = IngestionRunner(sessions, adapter)
    runner.operations.retry(actor, ns, active.operation.id, 1)
    runner.run(actor, ns, active.operation.id, 2)
    with pytest.raises(Conflict):
        service.admit(actor, ns, UploadRequest(request_key="accepted-retry", items=[declaration]))


def test_concurrent_reupload_admission_has_one_replacement(ready_ingestion, sessions):
    create, actor, ns, adapter = ready_ingestion
    service, original = create()
    service.operations.fail(actor, ns, original.operation.id, 1)
    with sessions() as session:
        payload = session.get(OperationRow, original.operation.id).inputs["items"][0]
    declaration = UploadDeclaration(**(payload | {"retry_of_item_id": str(original.items[0].id)}))
    barrier = Barrier(2)

    def admit(index):
        barrier.wait(timeout=5)
        try:
            return service.admit(
                actor, ns, UploadRequest(request_key=f"retry-{index}", items=[declaration])
            )
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(admit, range(2)))
    assert sum(outcome is not None for outcome in outcomes) == 1
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(IngestionItemRow)
                .where(IngestionItemRow.retry_of_item_id == original.items[0].id)
            )
            == 1
        )


def test_reupload_audit_failure_restores_origin_and_lineage_is_immutable(ready_ingestion, sessions):
    create, actor, ns, adapter = ready_ingestion
    service, original = create()
    service.operations.fail(actor, ns, original.operation.id, 1)
    with sessions() as session:
        payload = session.get(OperationRow, original.operation.id).inputs["items"][0]
    declaration = UploadDeclaration(**(payload | {"retry_of_item_id": str(original.items[0].id)}))
    command = UploadRequest(request_key="recover", items=[declaration])

    def fail(mapper, connection, row):
        if row.namespace_id == ns and row.action == "operation.ingestion_item_superseded":
            raise RuntimeError("reupload audit failure")

    event.listen(AuditRow, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="reupload audit failure"):
            service.admit(actor, ns, command)
    finally:
        event.remove(AuditRow, "before_insert", fail)
    assert service.get(actor, ns, original.operation.id).items[0].state == "ready"
    replacement = service.admit(actor, ns, command)
    with pytest.raises(DBAPIError, match="immutable"):
        with sessions.begin() as session:
            session.get(IngestionItemRow, replacement.items[0].id).retry_of_item_id = None
    with pytest.raises(DBAPIError, match="immutable"):
        with sessions.begin() as session:
            session.get(IngestionItemRow, original.items[0].id).state = "ready"


def test_direct_supersession_requires_replacement_at_commit(ready_ingestion, sessions):
    create, actor, ns, adapter = ready_ingestion
    service, original = create()
    service.operations.fail(actor, ns, original.operation.id, 1)
    with pytest.raises(DBAPIError, match="replacement in the same transaction"):
        with sessions.begin() as session:
            session.get(IngestionItemRow, original.items[0].id).state = "superseded"
    assert service.get(actor, ns, original.operation.id).items[0].state == "ready"


@pytest.mark.parametrize(
    "invalid", ["same_operation", "wrong_kind", "reused_artifact", "reused_staging"]
)
def test_database_reupload_requires_fresh_operation_and_storage(ready_ingestion, sessions, invalid):
    from verdant.operations import OperationRequest

    create, actor, ns, adapter = ready_ingestion
    service, original = create()
    service.operations.fail(actor, ns, original.operation.id, 1)
    if invalid == "same_operation":
        child_operation = original.operation.id
    else:
        child = service.operations.admit(
            actor,
            ns,
            OperationRequest(
                request_key=str(uuid4()),
                kind="qualification" if invalid == "wrong_kind" else "ingest_upload",
            ),
        )
        service.operations.start(actor, ns, child.id, 1)
        child_operation = child.id
    with pytest.raises(DBAPIError, match="fresh operation"):
        with sessions.begin() as session:
            origin = session.get(IngestionItemRow, original.items[0].id)
            origin.state = "superseded"
            session.flush()
            artifact = session.get(ArtifactRow, origin.artifact_id)
            new_artifact = artifact
            if invalid != "reused_artifact":
                new_artifact = ArtifactRow(
                    namespace_id=ns, sha256=artifact.sha256, size_bytes=artifact.size_bytes
                )
                session.add(new_artifact)
                session.flush()
            session.add(
                IngestionItemRow(
                    namespace_id=ns,
                    operation_id=child_operation,
                    position=1,
                    retry_of_item_id=origin.id,
                    artifact_id=new_artifact.id,
                    source_key=origin.source_key,
                    modality=origin.modality,
                    metadata_json=origin.metadata_json,
                    target_sample_id=origin.target_sample_id,
                    expected_revision=origin.expected_revision,
                    staging_key=origin.staging_key
                    if invalid == "reused_staging"
                    else f"{ns}/forged/{uuid4()}",
                    expires_at=origin.expires_at,
                )
            )
    assert service.get(actor, ns, original.operation.id).items[0].state == "ready"


def test_reupload_origin_namespace_and_current_grants_are_required(ready_ingestion, sessions):
    from verdant.domain import NotFound
    from verdant.persistence import NamespaceRow

    create, actor, ns, adapter = ready_ingestion
    service, original = create()
    service.operations.fail(actor, ns, original.operation.id, 1)
    with sessions() as session:
        payload = session.get(OperationRow, original.operation.id).inputs["items"][0]
    declaration = UploadDeclaration(**(payload | {"retry_of_item_id": str(original.items[0].id)}))
    other_namespace = uuid4()
    with sessions.begin() as session:
        session.add(NamespaceRow(id=other_namespace, name="other"))
    IdentityService(sessions).grant(actor.id, other_namespace, "write")
    command = UploadRequest(request_key="cross-namespace", items=[declaration])
    with pytest.raises(NotFound, match="origin not found"):
        service.admit(actor, other_namespace, command)
    IdentityService(sessions).revoke_grant(actor.id, ns)
    with pytest.raises(Forbidden):
        service.admit(actor, ns, command)
    with sessions() as session:
        assert session.get(IngestionItemRow, original.items[0].id).state == "ready"
        assert count(session, OperationRow, other_namespace) == 0


def test_maximum_batch_releases_each_spool_and_bounds_worker_memory(
    ready_ingestion, sessions, monkeypatch, record_property
):
    import tracemalloc

    import verdant.storage as storage_module

    create, actor, ns, adapter = ready_ingestion
    # Fixture bytes/upload precede measurement; the worker receives only durable IDs.
    body = b"b" * (2 * 1024 * 1024)
    _, batch = create([(f"bounded-{index}", body) for index in range(100)])
    aggregate = sum(item.size_bytes for item in batch.items)
    assert aggregate == 200 * 1024 * 1024
    original = storage_module.SpooledTemporaryFile
    spools = []

    def serial_spool(*args, **kwargs):
        assert all(spool.closed for spool in spools), "Worker retained a prior item's scratch"
        spool = original(*args, **kwargs)
        spools.append(spool)
        return spool

    monkeypatch.setattr(storage_module, "SpooledTemporaryFile", serial_spool)
    runner = IngestionRunner(sessions, adapter)
    tracemalloc.start()
    try:
        for expected_count in (25, 50, 75, 100):
            result = runner.run(actor, ns, batch.operation.id, 1, limit=25)
            assert sum(item.state == "accepted" for item in result.items) == expected_count
            assert result.operation.status == ("succeeded" if expected_count == 100 else "running")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(spools) == 100 and all(spool.closed and spool._rolled for spool in spools)
    record_property("batch_aggregate_artifact_bytes", aggregate)
    record_property("batch_worker_python_peak_bytes", peak)
    assert peak < 32 * 1024 * 1024
    assert runner.run(actor, ns, batch.operation.id, 1) == result
    with sessions() as session:
        assert count(session, SampleRow, ns) == count(session, SampleRevisionRow, ns) == 100
