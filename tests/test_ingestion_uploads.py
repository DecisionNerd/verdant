import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import httpx
import pytest
from sqlalchemy import func, select

from verdant.domain import Conflict, Forbidden
from verdant.identity import IdentityService
from verdant.ingestion import IngestionService, UploadDeclaration, UploadRequest
from verdant.persistence import ArtifactRow, EffectReceiptRow, IngestionItemRow, OperationEventRow
from verdant.storage import StorageUnavailable


def admit(registry, sessions, adapter, body=b"uploaded content"):
    _, actor, ns = registry
    service = IngestionService(sessions, bucket=adapter.settings.bucket, storage=adapter)
    batch = service.admit(
        actor,
        ns,
        UploadRequest(
            request_key="upload",
            items=[
                UploadDeclaration(
                    source_key="source",
                    modality="text",
                    sha256=hashlib.sha256(body).hexdigest(),
                    size_bytes=len(body),
                )
            ],
        ),
    )
    return service, actor, ns, batch


def owned_upload(sessions, item_id):
    with sessions() as session:
        row = session.get(IngestionItemRow, item_id)
        return row.staging_key, row.upload_id


def cleanup(adapter, key, upload_id):
    if upload_id:
        adapter.abort(key, upload_id)
    for pending in adapter.find_uploads(key):
        adapter.abort(key, pending)
    adapter.delete_one(key)


def test_real_upload_signing_progress_and_service_reload(registry, sessions, storage):
    adapter, _ = storage
    body = b"uploaded content"
    service, actor, ns, batch = admit(registry, sessions, adapter, body)
    operation, item = batch.operation.id, batch.items[0].id
    key, upload_id = owned_upload(sessions, item)
    try:
        first = service.initiate_upload(actor, ns, operation, item, 1)
        assert first.items[0].state == "uploading"
        key, upload_id = owned_upload(sessions, item)
        assert adapter.find_uploads(key) == [upload_id]
        signed = service.sign_upload_part(actor, ns, operation, item, 1, 1)
        assert signed.size_bytes == len(body) and signed.expires_in == 120
        assert "url=" not in repr(signed)
        with httpx.Client(timeout=10, trust_env=False) as http:
            assert http.put(signed.url, content=body + b"excess").status_code == 403
            http.put(signed.url, content=body).raise_for_status()
        resumed = IngestionService(sessions, bucket=adapter.settings.bucket, storage=adapter)
        assert resumed.initiate_upload(actor, ns, operation, item, 1) == first
        parts = resumed.upload_progress(actor, ns, operation, item, 1).parts
        assert [(p.part_number, p.size_bytes) for p in parts] == [(1, len(body))]
        with pytest.raises(Conflict, match="outside"):
            resumed.sign_upload_part(actor, ns, operation, item, 1, 2)
        with pytest.raises(Conflict, match="stale"):
            resumed.sign_upload_part(actor, ns, operation, item, 2, 1)
        completed = resumed.complete_upload(actor, ns, operation, item, 1)
        assert completed.operation.status == "queued" and completed.items[0].state == "ready"
        assert resumed.complete_upload(actor, ns, operation, item, 1) == completed
        with sessions() as session:
            assert session.get(ArtifactRow, completed.items[0].artifact_id).verified_at is None
    finally:
        cleanup(adapter, key, upload_id)


def test_lost_initiation_response_reconciles_one_actual_upload(
    registry, sessions, storage, monkeypatch
):
    adapter, _ = storage
    service, actor, ns, batch = admit(registry, sessions, adapter)
    operation, item = batch.operation.id, batch.items[0].id
    key, _ = owned_upload(sessions, item)
    original = adapter.initiate
    calls = []

    def lose_response(key):
        calls.append(key)
        original(key)
        raise StorageUnavailable("Initiation response was lost")

    monkeypatch.setattr(adapter, "initiate", lose_response)
    try:
        with pytest.raises(StorageUnavailable):
            service.initiate_upload(actor, ns, operation, item, 1)
        with sessions() as session:
            receipt = session.scalar(
                select(EffectReceiptRow).where(EffectReceiptRow.operation_id == operation)
            )
            assert receipt.status == "unknown"
        assert service.initiate_upload(actor, ns, operation, item, 1).items[0].state == "uploading"
        key, upload_id = owned_upload(sessions, item)
        assert calls == [key] and adapter.find_uploads(key) == [upload_id]
        with sessions() as session:
            receipt = session.scalar(
                select(EffectReceiptRow).where(EffectReceiptRow.operation_id == operation)
            )
            assert receipt.status == "succeeded" and receipt.provider_reference == upload_id
    finally:
        cleanup(adapter, key, None)


def test_overlapping_initiation_never_replays_unknown_write(
    registry, sessions, storage, monkeypatch
):
    adapter, _ = storage
    service, actor, ns, batch = admit(registry, sessions, adapter)
    operation, item = batch.operation.id, batch.items[0].id
    key, _ = owned_upload(sessions, item)
    original = adapter.initiate
    started, release = Event(), Event()
    calls = []

    def paused(key):
        calls.append(key)
        assert sessions.kw["bind"].pool.checkedout() == 0
        started.set()
        assert release.wait(timeout=5)
        return original(key)

    monkeypatch.setattr(adapter, "initiate", paused)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(service.initiate_upload, actor, ns, operation, item, 1)
            try:
                assert started.wait(timeout=5)
                with pytest.raises(Conflict, match="uncertain"):
                    service.initiate_upload(actor, ns, operation, item, 1)
            finally:
                release.set()
            assert future.result(timeout=10).items[0].state == "uploading"
        assert calls == [key] and len(adapter.find_uploads(key)) == 1
    finally:
        cleanup(adapter, key, None)


def test_revoked_and_expired_uploads_deny_before_provider(registry, sessions, storage, monkeypatch):
    adapter, _ = storage
    service, actor, ns, batch = admit(registry, sessions, adapter)
    operation, item = batch.operation.id, batch.items[0].id
    IdentityService(sessions).revoke_grant(actor.id, ns)

    def unexpected(*args, **kwargs):
        pytest.fail("Denied upload reached provider")

    monkeypatch.setattr(adapter, "initiate", unexpected)
    with pytest.raises(Forbidden):
        service.initiate_upload(actor, ns, operation, item, 1)
    IdentityService(sessions).grant(actor.id, ns, "write")
    # Fixed TTL is changed only in this test to produce an already-expired declaration.
    monkeypatch.setattr("verdant.ingestion.UPLOAD_TTL", timedelta(seconds=-1))
    expired = service.admit(
        actor,
        ns,
        UploadRequest(
            request_key="expired",
            items=[
                UploadDeclaration(
                    source_key="expired",
                    modality="text",
                    sha256="a" * 64,
                    size_bytes=1,
                )
            ],
        ),
    )
    with pytest.raises(Conflict, match="expired"):
        service.initiate_upload(actor, ns, expired.operation.id, expired.items[0].id, 1)


@pytest.mark.parametrize("changed", ["store", "bucket"])
def test_changed_store_configuration_denies_before_provider(
    registry, sessions, storage, monkeypatch, changed
):
    from dataclasses import replace

    from verdant.storage import S3Storage

    adapter, _ = storage
    _, actor, ns, batch = admit(registry, sessions, adapter)
    if changed == "bucket":
        adapter = S3Storage(replace(adapter.settings, bucket="different-bucket"))
    service = IngestionService(
        sessions,
        bucket=adapter.settings.bucket,
        store_id="different-store" if changed == "store" else "primary",
        storage=adapter,
    )

    def unexpected(*args, **kwargs):
        pytest.fail("Mismatched placement reached provider")

    monkeypatch.setattr(adapter, "initiate", unexpected)
    try:
        with pytest.raises(StorageUnavailable, match="Stored upload location"):
            service.initiate_upload(actor, ns, batch.operation.id, batch.items[0].id, 1)
    finally:
        if changed == "bucket":
            adapter.client.close()
            adapter.signer.close()


def test_incomplete_parts_are_repairable_before_completion(registry, sessions, storage):
    adapter, _ = storage
    body = b"repaired upload"
    service, actor, ns, batch = admit(registry, sessions, adapter, body)
    operation, item = batch.operation.id, batch.items[0].id
    key, _ = owned_upload(sessions, item)
    try:
        service.initiate_upload(actor, ns, operation, item, 1)
        with pytest.raises(Conflict, match="incomplete or invalid"):
            service.complete_upload(actor, ns, operation, item, 1)
        signed = service.sign_upload_part(actor, ns, operation, item, 1, 1)
        with httpx.Client(timeout=10, trust_env=False) as http:
            http.put(signed.url, content=body).raise_for_status()
        assert service.complete_upload(actor, ns, operation, item, 1).items[0].state == "ready"
    finally:
        cleanup(adapter, key, None)


def test_completion_response_loss_adopts_staging_without_replay(
    registry, sessions, storage, monkeypatch
):
    adapter, _ = storage
    body = b"completed once"
    service, actor, ns, batch = admit(registry, sessions, adapter, body)
    operation, item = batch.operation.id, batch.items[0].id
    key, _ = owned_upload(sessions, item)
    original = adapter.complete
    calls = []

    def lose_response(*args):
        calls.append(args)
        original(*args)
        raise StorageUnavailable("Completion response lost")

    try:
        service.initiate_upload(actor, ns, operation, item, 1)
        signed = service.sign_upload_part(actor, ns, operation, item, 1, 1)
        with httpx.Client(timeout=10, trust_env=False) as http:
            http.put(signed.url, content=body).raise_for_status()
        monkeypatch.setattr(adapter, "complete", lose_response)
        with pytest.raises(StorageUnavailable):
            service.complete_upload(actor, ns, operation, item, 1)
        with pytest.raises(Conflict, match="Completion is in progress"):
            service.sign_upload_part(actor, ns, operation, item, 1, 1)
        assert service.complete_upload(actor, ns, operation, item, 1).operation.status == "queued"
        assert len(calls) == 1
    finally:
        cleanup(adapter, key, None)


def test_all_items_ready_emit_one_queue_event(registry, sessions, storage):
    adapter, _ = storage
    _, actor, ns = registry
    service = IngestionService(sessions, bucket=adapter.settings.bucket, storage=adapter)
    batch = service.admit(
        actor,
        ns,
        UploadRequest(
            request_key="two",
            items=[
                UploadDeclaration(
                    source_key=f"source-{i}",
                    modality="text",
                    sha256=hashlib.sha256(b"x").hexdigest(),
                    size_bytes=1,
                )
                for i in range(2)
            ],
        ),
    )
    operation = batch.operation.id
    keys = []
    try:
        for position, item in enumerate(batch.items):
            key, _ = owned_upload(sessions, item.id)
            keys.append(key)
            service.initiate_upload(actor, ns, operation, item.id, 1)
            signed = service.sign_upload_part(actor, ns, operation, item.id, 1, 1)
            with httpx.Client(timeout=10, trust_env=False) as http:
                http.put(signed.url, content=b"x").raise_for_status()
            completed = service.complete_upload(actor, ns, operation, item.id, 1)
            assert completed.operation.status == ("running" if position == 0 else "queued")
        service.complete_upload(actor, ns, operation, batch.items[0].id, 1)
        with sessions() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(OperationEventRow)
                    .where(
                        OperationEventRow.operation_id == operation,
                        OperationEventRow.event_type == "operation.queued",
                    )
                )
                == 1
            )
    finally:
        for key in keys:
            cleanup(adapter, key, None)


def test_revocation_during_completion_blocks_ready_acceptance(
    registry, sessions, storage, monkeypatch
):
    adapter, _ = storage
    body = b"revoke before acceptance"
    service, actor, ns, batch = admit(registry, sessions, adapter, body)
    operation, item = batch.operation.id, batch.items[0].id
    key, _ = owned_upload(sessions, item)
    original = adapter.complete

    def revoke_after_write(*args):
        original(*args)
        IdentityService(sessions).revoke_grant(actor.id, ns)

    try:
        service.initiate_upload(actor, ns, operation, item, 1)
        signed = service.sign_upload_part(actor, ns, operation, item, 1, 1)
        with httpx.Client(timeout=10, trust_env=False) as http:
            http.put(signed.url, content=body).raise_for_status()
        monkeypatch.setattr(adapter, "complete", revoke_after_write)
        with pytest.raises(Forbidden):
            service.complete_upload(actor, ns, operation, item, 1)
        with sessions() as session:
            assert session.get(IngestionItemRow, item).state == "uploading"
        IdentityService(sessions).grant(actor.id, ns, "write")
        assert service.complete_upload(actor, ns, operation, item, 1).items[0].state == "ready"
    finally:
        cleanup(adapter, key, None)


def test_unknown_completion_with_incomplete_parts_can_be_repaired(
    registry, sessions, storage, monkeypatch
):
    adapter, _ = storage
    body = b"repair after provider outage"
    service, actor, ns, batch = admit(registry, sessions, adapter, body)
    operation, item = batch.operation.id, batch.items[0].id
    key, _ = owned_upload(sessions, item)
    original = adapter.complete

    def unavailable(*args):
        raise StorageUnavailable("Provider unavailable before part preflight")

    try:
        service.initiate_upload(actor, ns, operation, item, 1)
        monkeypatch.setattr(adapter, "complete", unavailable)
        with pytest.raises(StorageUnavailable):
            service.complete_upload(actor, ns, operation, item, 1)
        monkeypatch.setattr(adapter, "complete", original)
        with pytest.raises(Conflict, match="incomplete or invalid"):
            service.complete_upload(actor, ns, operation, item, 1)
        with sessions() as session:
            receipt = session.scalar(
                select(EffectReceiptRow).where(
                    EffectReceiptRow.operation_id == operation,
                    EffectReceiptRow.identity["kind"].as_string() == "artifact.multipart.complete",
                )
            )
            assert receipt.status == "unknown"
        signed = service.sign_upload_part(actor, ns, operation, item, 1, 1)
        with httpx.Client(timeout=10, trust_env=False) as http:
            http.put(signed.url, content=body).raise_for_status()
        assert service.complete_upload(actor, ns, operation, item, 1).items[0].state == "ready"
    finally:
        cleanup(adapter, key, None)


def test_completion_during_signing_prevents_url_disclosure(
    registry, sessions, storage, monkeypatch
):
    adapter, _ = storage
    body = b"finish while signing"
    service, actor, ns, batch = admit(registry, sessions, adapter, body)
    operation, item = batch.operation.id, batch.items[0].id
    key, _ = owned_upload(sessions, item)
    original = adapter.sign_part
    try:
        service.initiate_upload(actor, ns, operation, item, 1)
        signed = service.sign_upload_part(actor, ns, operation, item, 1, 1)
        with httpx.Client(timeout=10, trust_env=False) as http:
            http.put(signed.url, content=body).raise_for_status()

        def finish_during_sign(*args):
            url = original(*args)
            service.complete_upload(actor, ns, operation, item, 1)
            return url

        monkeypatch.setattr(adapter, "sign_part", finish_during_sign)
        with pytest.raises(Conflict):
            service.sign_upload_part(actor, ns, operation, item, 1, 1)
        assert service.get(actor, ns, operation).items[0].state == "ready"
    finally:
        cleanup(adapter, key, None)


def test_revocation_during_final_unknown_upload_probe_prevents_url_disclosure(
    registry, sessions, storage, monkeypatch
):
    adapter, _ = storage
    service, actor, ns, batch = admit(registry, sessions, adapter)
    operation, item = batch.operation.id, batch.items[0].id
    key, _ = owned_upload(sessions, item)

    def unavailable(*args):
        raise StorageUnavailable("Provider unavailable before completion preflight")

    original_parts = adapter.parts
    probes = []

    def revoke_during_final_probe(*args):
        result = original_parts(*args)
        probes.append(args)
        if len(probes) == 2:
            IdentityService(sessions).revoke_grant(actor.id, ns)
        return result

    try:
        service.initiate_upload(actor, ns, operation, item, 1)
        monkeypatch.setattr(adapter, "complete", unavailable)
        with pytest.raises(StorageUnavailable):
            service.complete_upload(actor, ns, operation, item, 1)
        monkeypatch.setattr(adapter, "parts", revoke_during_final_probe)
        with pytest.raises(Forbidden):
            service.sign_upload_part(actor, ns, operation, item, 1, 1)
        assert len(probes) == 2
    finally:
        cleanup(adapter, key, None)
