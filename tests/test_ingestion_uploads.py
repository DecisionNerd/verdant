import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import httpx
import pytest
from sqlalchemy import select

from verdant.domain import Conflict, Forbidden
from verdant.identity import IdentityService
from verdant.ingestion import IngestionService, UploadDeclaration, UploadRequest
from verdant.persistence import EffectReceiptRow, IngestionItemRow
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
