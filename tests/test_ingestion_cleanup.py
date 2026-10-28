import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from verdant.domain import Conflict, Forbidden
from verdant.identity import IdentityService
from verdant.ingestion import IngestionService, UploadDeclaration, UploadRequest
from verdant.ingestion_cleanup import IngestionCleanup
from verdant.ingestion_runner import IngestionRunner
from verdant.operations import EffectReconciliation, WorkflowReconciliation
from verdant.persistence import ArtifactRow, EffectReceiptRow, IngestionItemRow, OperationEventRow
from verdant.storage import ObjectDigest, ObjectMissing, StorageUnavailable


@pytest.fixture
def pending_upload(registry, sessions, storage):
    _, actor, ns = registry
    adapter, _ = storage
    owned = []

    def create(*, initiate=True, upload=True):
        body = b"pending upload"
        service = IngestionService(sessions, bucket=adapter.settings.bucket, storage=adapter)
        batch = service.admit(
            actor,
            ns,
            UploadRequest(
                request_key=str(uuid4()),
                items=[
                    UploadDeclaration(
                        source_key=str(uuid4()),
                        modality="text",
                        sha256=hashlib.sha256(body).hexdigest(),
                        size_bytes=len(body),
                    )
                ],
            ),
        )
        item = batch.items[0]
        with sessions() as session:
            key = session.get(IngestionItemRow, item.id).staging_key
        owned.append(key)
        if initiate:
            service.initiate_upload(actor, ns, batch.operation.id, item.id, 1)
        if upload:
            signed = service.sign_upload_part(actor, ns, batch.operation.id, item.id, 1, 1)
            with httpx.Client(timeout=10, trust_env=False) as http:
                http.put(signed.url, content=body).raise_for_status()
        return service, batch

    yield create, actor, ns, adapter
    for key in owned:
        for upload_id in adapter.find_uploads(key):
            adapter.abort(key, upload_id)
        adapter.delete_one(key)


def test_bounded_cancel_cleanup_preserves_accepted_artifact(ready_ingestion, sessions):
    create, actor, ns, adapter = ready_ingestion
    _, batch = create([("first", b"first"), ("second", b"second")])
    partial = IngestionRunner(sessions, adapter).run(actor, ns, batch.operation.id, 1, limit=1)
    first = partial.items[0]
    cleanup = IngestionCleanup(sessions, adapter)
    cleanup.request_cancel(actor, ns, batch.operation.id, 1)
    one = cleanup.run(actor, ns, batch.operation.id, 1, limit=1)
    assert one.cleaned_items == 1 and one.remaining_items == 1
    assert one.batch.operation.status == "cancellation_requested" and one.batch.items[0] == first
    done = cleanup.run(actor, ns, batch.operation.id, 1, limit=1)
    assert done.batch.operation.status == "cancelled" and done.remaining_items == 0
    assert done.batch.items[0] == first and done.batch.items[1].state == "cancelled"
    assert cleanup.run(actor, ns, batch.operation.id, 1) == done
    with sessions() as session:
        artifact = session.get(ArtifactRow, first.artifact_id)
        assert artifact.verified_at is not None
        key = f"{ns}/artifacts/{artifact.id}"
    adapter.verify(key, ObjectDigest(first.sha256, first.size_bytes))


def test_successful_operation_staging_cleanup_keeps_final_bytes(ready_ingestion, sessions):
    create, actor, ns, adapter = ready_ingestion
    _, batch = create()
    done = IngestionRunner(sessions, adapter).run(actor, ns, batch.operation.id, 1)
    result = IngestionCleanup(sessions, adapter).run(actor, ns, batch.operation.id, 1)
    assert result.batch.operation.status == "succeeded" and result.batch.items == done.items
    assert result.remaining_items == 0 and not result.pending_reconciliation
    item = done.items[0]
    adapter.verify(f"{ns}/artifacts/{item.artifact_id}", ObjectDigest(item.sha256, item.size_bytes))


def test_lost_delete_response_reconciles_idempotent_cleanup(ready_ingestion, sessions, monkeypatch):
    create, actor, ns, adapter = ready_ingestion
    _, batch = create()
    cleanup = IngestionCleanup(sessions, adapter)
    cleanup.request_cancel(actor, ns, batch.operation.id, 1)
    original = adapter.delete_one

    def lose_response(key):
        original(key)
        raise StorageUnavailable("Delete response lost")

    monkeypatch.setattr(adapter, "delete_one", lose_response)
    with pytest.raises(StorageUnavailable):
        cleanup.run(actor, ns, batch.operation.id, 1)
    with sessions() as session:
        receipt = session.scalar(
            select(EffectReceiptRow).where(
                EffectReceiptRow.operation_id == batch.operation.id,
                EffectReceiptRow.identity["kind"].as_string() == "artifact.staging.cleanup",
            )
        )
        assert receipt.status == "unknown"
        assert session.get(IngestionItemRow, batch.items[0].id).state == "ready"
    monkeypatch.setattr(adapter, "delete_one", original)
    assert cleanup.run(actor, ns, batch.operation.id, 1).batch.operation.status == "cancelled"


@pytest.mark.parametrize("phase", ["initiation", "completion"])
def test_late_provider_write_keeps_cancel_pending_until_observed(
    pending_upload, sessions, monkeypatch, phase
):
    create, actor, ns, adapter = pending_upload
    service, batch = create(initiate=phase == "completion", upload=phase == "completion")
    cleanup = IngestionCleanup(sessions, adapter)
    started, release = Event(), Event()
    method = "initiate" if phase == "initiation" else "complete"
    original = getattr(adapter, method)

    def delayed(*args):
        started.set()
        assert release.wait(timeout=5)
        return original(*args)

    monkeypatch.setattr(adapter, method, delayed)
    command = service.initiate_upload if phase == "initiation" else service.complete_upload
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(command, actor, ns, batch.operation.id, batch.items[0].id, 1)
        try:
            assert started.wait(timeout=5)
            cleanup.request_cancel(actor, ns, batch.operation.id, 1)
            pending = cleanup.run(actor, ns, batch.operation.id, 1)
            assert pending.pending_reconciliation and pending.remaining_items == 1
            assert pending.batch.operation.status == "cancellation_requested"
        finally:
            release.set()
        with pytest.raises(Conflict):
            future.result(timeout=10)
    done = cleanup.run(actor, ns, batch.operation.id, 1)
    assert done.batch.operation.status == "cancelled" and done.remaining_items == 0
    with sessions() as session:
        key = session.get(IngestionItemRow, batch.items[0].id).staging_key
    assert adapter.find_uploads(key) == []
    with pytest.raises(ObjectMissing):
        adapter.size(key)


def test_missing_promotion_fact_requires_explicit_reconciliation(
    ready_ingestion, sessions, monkeypatch
):
    create, actor, ns, adapter = ready_ingestion
    _, batch = create()
    runner = IngestionRunner(sessions, adapter)

    def interrupted(*args):
        raise RuntimeError("stopped before any provider promotion request")

    monkeypatch.setattr(adapter, "promote", interrupted)
    with pytest.raises(RuntimeError):
        runner.run(actor, ns, batch.operation.id, 1)
    cleanup = IngestionCleanup(sessions, adapter)
    cleanup.request_cancel(actor, ns, batch.operation.id, 1)
    pending = cleanup.run(actor, ns, batch.operation.id, 1)
    assert (
        pending.pending_reconciliation
        and pending.batch.operation.status == "cancellation_requested"
    )
    with sessions() as session:
        item = session.get(IngestionItemRow, batch.items[0].id)
        assert adapter.size(item.staging_key) == pending.batch.items[0].size_bytes
        receipt = session.scalar(
            select(EffectReceiptRow).where(
                EffectReceiptRow.operation_id == batch.operation.id,
                EffectReceiptRow.identity["kind"].as_string() == "artifact.promote",
            )
        )
        receipt_id, revision = receipt.id, receipt.revision
    runner.operations.reconcile_effect(
        actor,
        ns,
        batch.operation.id,
        receipt_id,
        EffectReconciliation(
            expected_revision=revision,
            outcome="absent",
            evidence="Test stopped before provider invocation; no native request was issued",
        ),
    )
    assert cleanup.run(actor, ns, batch.operation.id, 1).batch.operation.status == "cancelled"


def test_expired_upload_and_current_grants_are_enforced(pending_upload, sessions, monkeypatch):
    create, actor, ns, adapter = pending_upload
    service, active = create(initiate=False, upload=False)
    cleanup = IngestionCleanup(sessions, adapter)
    with pytest.raises(Conflict, match="no expired"):
        cleanup.request_cancel(actor, ns, active.operation.id, 1, expired_only=True)
    monkeypatch.setattr("verdant.ingestion.UPLOAD_TTL", timedelta(seconds=-1))
    _, expired = create(initiate=False, upload=False)
    cleanup.request_cancel(actor, ns, expired.operation.id, 1, expired_only=True)
    assert cleanup.run(actor, ns, expired.operation.id, 1).batch.operation.status == "cancelled"
    cleanup.request_cancel(actor, ns, active.operation.id, 1)
    with pytest.raises(Conflict, match="stale"):
        cleanup.run(actor, ns, active.operation.id, 2)
    IdentityService(sessions).revoke_grant(actor.id, ns)
    with pytest.raises(Forbidden):
        cleanup.run(actor, ns, active.operation.id, 1)


def test_unknown_workflow_admission_blocks_terminal_cancellation(ready_ingestion, sessions):
    create, actor, ns, adapter = ready_ingestion
    service, batch = create()
    with sessions() as session:
        event_id = session.scalar(
            select(OperationEventRow.id).where(
                OperationEventRow.operation_id == batch.operation.id,
                OperationEventRow.event_type == "operation.queued",
            )
        )
    service.operations.receive_workflow_event(actor, ns, event_id)
    unknown = service.operations.begin_workflow_submission(
        actor, ns, batch.operation.id, 1, uuid4()
    )
    cleanup = IngestionCleanup(sessions, adapter)
    cleanup.request_cancel(actor, ns, batch.operation.id, 1)
    pending = cleanup.run(actor, ns, batch.operation.id, 1)
    assert pending.pending_reconciliation and pending.remaining_items == 0
    assert pending.batch.operation.status == "cancellation_requested"
    service.operations.reconcile_workflow_submission(
        actor,
        ns,
        batch.operation.id,
        1,
        WorkflowReconciliation(
            request_digest=unknown.request_digest,
            provider_run_id=uuid4(),
            evidence="Fixture only: known provider admission identity, no live Prefect call made",
        ),
    )
    assert cleanup.run(actor, ns, batch.operation.id, 1).batch.operation.status == "cancelled"


def test_cleanup_cursor_can_pass_an_uncertain_item(ready_ingestion, sessions, monkeypatch):
    create, actor, ns, adapter = ready_ingestion
    _, batch = create([("first", b"first"), ("second", b"second")])

    def interrupted(*args):
        raise RuntimeError("no provider call issued")

    monkeypatch.setattr(adapter, "promote", interrupted)
    with pytest.raises(RuntimeError):
        IngestionRunner(sessions, adapter).run(actor, ns, batch.operation.id, 1, limit=1)
    cleanup = IngestionCleanup(sessions, adapter)
    cleanup.request_cancel(actor, ns, batch.operation.id, 1)
    first = cleanup.run(actor, ns, batch.operation.id, 1, limit=1)
    assert first.pending_reconciliation and first.next_after_position == 0
    next_page = cleanup.run(actor, ns, batch.operation.id, 1, limit=1, after_position=0)
    assert next_page.cleaned_items == 1 and next_page.remaining_items == 1
    assert next_page.pending_reconciliation and next_page.next_after_position is None
    assert next_page.batch.operation.status == "cancellation_requested"
    with sessions() as session:
        staging = session.get(IngestionItemRow, batch.items[1].id).staging_key
    with pytest.raises(ObjectMissing):
        adapter.size(staging)


def test_wrong_staging_location_cannot_delete_verified_artifact(
    ready_ingestion, sessions, monkeypatch
):
    create, actor, ns, adapter = ready_ingestion
    _, batch = create()
    done = IngestionRunner(sessions, adapter).run(actor, ns, batch.operation.id, 1)
    accepted = done.items[0]
    final_key = f"{ns}/artifacts/{accepted.artifact_id}"
    with sessions.begin() as session:
        item = session.get(IngestionItemRow, accepted.id)
        # Deliberately malformed database fixture; no user command accepts a staging key.
        session.add(
            IngestionItemRow(
                namespace_id=ns,
                operation_id=batch.operation.id,
                position=1,
                artifact_id=accepted.artifact_id,
                source_key="fault",
                modality="text",
                metadata_json={},
                staging_key=final_key,
                expires_at=item.expires_at,
            )
        )
    original = adapter.delete_one
    deleted = []

    def record(key):
        deleted.append(key)
        return original(key)

    monkeypatch.setattr(adapter, "delete_one", record)
    with pytest.raises(Conflict, match="domain-owned staging"):
        IngestionCleanup(sessions, adapter).run(actor, ns, batch.operation.id, 1)
    assert final_key not in deleted
    adapter.verify(final_key, ObjectDigest(accepted.sha256, accepted.size_bytes))


def test_revocation_after_delete_keeps_receipt_and_cancel_uncommitted(
    ready_ingestion, sessions, monkeypatch
):
    create, actor, ns, adapter = ready_ingestion
    _, batch = create()
    cleanup = IngestionCleanup(sessions, adapter)
    cleanup.request_cancel(actor, ns, batch.operation.id, 1)
    original = adapter.delete_one

    def revoke_after_delete(key):
        result = original(key)
        IdentityService(sessions).revoke_grant(actor.id, ns)
        return result

    monkeypatch.setattr(adapter, "delete_one", revoke_after_delete)
    with pytest.raises(Forbidden):
        cleanup.run(actor, ns, batch.operation.id, 1)
    with sessions() as session:
        receipt = session.scalar(
            select(EffectReceiptRow).where(
                EffectReceiptRow.operation_id == batch.operation.id,
                EffectReceiptRow.identity["kind"].as_string() == "artifact.staging.cleanup",
            )
        )
        assert receipt.status == "unknown"
    IdentityService(sessions).grant(actor.id, ns, "write")
    monkeypatch.setattr(adapter, "delete_one", original)
    assert cleanup.run(actor, ns, batch.operation.id, 1).batch.operation.status == "cancelled"


def test_duplicate_cleanup_workers_share_one_effect(ready_ingestion, sessions, monkeypatch):
    from threading import Barrier

    create, actor, ns, adapter = ready_ingestion
    _, batch = create()
    cleanup = IngestionCleanup(sessions, adapter)
    cleanup.request_cancel(actor, ns, batch.operation.id, 1)
    barrier = Barrier(2)
    original = adapter.delete_one

    def overlap(key):
        barrier.wait(timeout=10)
        return original(key)

    monkeypatch.setattr(adapter, "delete_one", overlap)

    def run(_):
        return IngestionCleanup(sessions, adapter).run(actor, ns, batch.operation.id, 1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(run, range(2))
    assert first == second and first.batch.operation.status == "cancelled"
    with sessions() as session:
        receipts = session.scalars(
            select(EffectReceiptRow).where(
                EffectReceiptRow.operation_id == batch.operation.id,
                EffectReceiptRow.identity["kind"].as_string() == "artifact.staging.cleanup",
            )
        ).all()
        assert len(receipts) == 1 and receipts[0].status == "succeeded"
