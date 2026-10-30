"""Real SIGKILL at controlled boundaries against isolated PostgreSQL and S3."""

import multiprocessing
import signal

import pytest
from sqlalchemy import func, select

from verdant.ingestion_runner import IngestionRunner
from verdant.persistence import (
    ArtifactRow,
    EffectReceiptRow,
    IngestionItemRow,
    SampleRevisionRow,
    SampleRow,
    make_sessions,
)
from verdant.storage import ObjectDigest, ObjectMissing, S3Storage


def interrupted_worker(url, settings, actor, namespace, operation, boundary, reached):
    # Spawn owns fresh connections; never inherit a parent's DB pool or S3 client.
    sessions = make_sessions(url)
    storage = S3Storage(settings)
    original = storage.promote

    def promote(*args, **kwargs):
        if boundary == "after_promotion":
            original(*args, **kwargs)
        reached.set()
        # Parent kills this process while no database transaction is held.
        signal.pause()
        raise RuntimeError("Qualification worker received an unexpected signal")

    storage.promote = promote
    try:
        IngestionRunner(sessions, storage).run(actor, namespace, operation, 1)
    finally:
        storage.close()
        sessions.kw["bind"].dispose()


@pytest.mark.parametrize("boundary", ["before_promotion", "after_promotion"])
def test_sigkill_preserves_unknown_effect_and_recovers_once(ready_ingestion, sessions, boundary):
    create, actor, ns, adapter = ready_ingestion
    service, batch = create()
    item = batch.items[0]
    context = multiprocessing.get_context("spawn")
    reached = context.Event()
    process = context.Process(
        target=interrupted_worker,
        args=(
            sessions.kw["bind"].url.render_as_string(hide_password=False),
            adapter.settings,
            actor,
            ns,
            batch.operation.id,
            boundary,
            reached,
        ),
    )
    process.start()
    try:
        assert reached.wait(20), "Worker did not reach the controlled interruption boundary"
        process.kill()
        process.join(10)
        assert process.exitcode == -signal.SIGKILL
        with sessions() as session:
            stored = session.get(IngestionItemRow, item.id)
            staging = stored.staging_key
            assert stored.state == "ready"
            assert session.get(ArtifactRow, item.artifact_id).verified_at is None
            receipt = session.scalar(
                select(EffectReceiptRow).where(
                    EffectReceiptRow.operation_id == batch.operation.id,
                    EffectReceiptRow.identity["kind"].as_string() == "artifact.promote",
                )
            )
            assert receipt.status == "unknown"
            receipt_id = receipt.id
            assert (
                session.scalar(
                    select(func.count()).select_from(SampleRow).where(SampleRow.namespace_id == ns)
                )
                == 0
            )
        assert service.get(actor, ns, batch.operation.id).operation.status == "running"
        final = f"{ns}/artifacts/{item.artifact_id}"
        expected = ObjectDigest(item.sha256, item.size_bytes)
        if boundary == "after_promotion":
            adapter.verify(final, expected)
            # Recover from the immutable final alone, with original staging unavailable.
            adapter.delete_one(staging)
        else:
            with pytest.raises(ObjectMissing):
                adapter.size(final)
        fresh = IngestionRunner(sessions, adapter)
        accepted = fresh.run(actor, ns, batch.operation.id, 1)
        assert accepted.operation.status == "succeeded"
        assert fresh.run(actor, ns, batch.operation.id, 1) == accepted
        adapter.verify(final, expected)
        with sessions() as session:
            for model in (SampleRow, SampleRevisionRow):
                assert (
                    session.scalar(
                        select(func.count()).select_from(model).where(model.namespace_id == ns)
                    )
                    == 1
                )
            receipts = session.scalars(
                select(EffectReceiptRow).where(
                    EffectReceiptRow.operation_id == batch.operation.id,
                    EffectReceiptRow.identity["kind"].as_string() == "artifact.promote",
                )
            ).all()
            assert len(receipts) == 1
            assert receipts[0].id == receipt_id and receipts[0].status == "succeeded"
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        process.close()
