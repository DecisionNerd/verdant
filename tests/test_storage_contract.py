"""Opt-in backend contract through production S3 transport, on an owned disposable prefix.

Run against each explicitly enabled backend with VERDANT_STORAGE_TEST_ENDPOINT,
VERDANT_STORAGE_TEST_BUCKET and the SDK's ordinary credential chain. No bucket or
CORS configuration is changed. The existing private bucket must permit the suite.
"""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest

from verdant.storage import (
    PART_BYTES,
    ObjectCorrupt,
    ObjectDigest,
    ObjectMissing,
    S3Settings,
    S3Storage,
    StorageUnavailable,
)


def digest(body):
    return ObjectDigest(hashlib.sha256(body).hexdigest(), len(body))


def upload(adapter, key, body):
    upload_id = adapter.initiate(key)
    try:
        with httpx.Client(timeout=30, trust_env=False) as http:
            chunks = [body[i : i + PART_BYTES] for i in range(0, len(body), PART_BYTES)] or [b""]
            for number, chunk in enumerate(chunks, 1):
                response = http.put(
                    adapter.sign_part(key, upload_id, number, len(chunk)), content=chunk
                )
                response.raise_for_status()
            assert len(adapter.parts(key, upload_id)) == len(chunks)
        adapter.complete(key, upload_id, len(body))
    finally:
        adapter.abort(key, upload_id)


def test_multipart_resume_promotion_and_replayable_staging(storage):
    adapter, prefix = storage
    staging, final = prefix + "staging", prefix + "final"
    body = b"a" * PART_BYTES + b"last part"
    expected = digest(body)
    upload(adapter, staging, body)
    assert adapter.promote(staging, final, expected) == expected
    # Idempotent promotion uses conditional final creation and independent digest verification.
    assert adapter.promote(staging, final, expected) == expected
    assert adapter.read_range(final, 2, 7) == body[2:8]
    with httpx.Client(timeout=10, trust_env=False) as http:
        signed = adapter.sign_read(final)
        response = http.get(signed)
        assert response.status_code == 200 and response.content == body
        assert "attachment" in response.headers["content-disposition"]
        assert http.get(signed, headers={"Host": "changed.invalid"}).status_code == 403
        assert http.get(signed.replace("/final?", "/other?")).status_code == 403
    # Simulate replay of browser staging access after acceptance. Final bytes stay pinned.
    upload(adapter, staging, b"changed bytes")
    assert adapter.verify(final, expected) == expected
    assert adapter.promote(staging, final, expected) == expected
    adapter.delete_one(staging)
    assert adapter.promote(staging, final, expected) == expected


def test_concurrent_final_creation_and_conflicting_content(storage):
    adapter, prefix = storage
    body = b"concurrent immutable bytes"
    upload(adapter, prefix + "staging", body)
    barrier = Barrier(2)

    def promote(_):
        barrier.wait(timeout=10)
        return adapter.promote(prefix + "staging", prefix + "final", digest(body))

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(promote, range(2))) == [digest(body)] * 2
    upload(adapter, prefix + "other", b"conflicting immutable bytes")
    with pytest.raises(ObjectCorrupt):
        adapter.promote(prefix + "other", prefix + "final", digest(b"conflicting immutable bytes"))
    assert adapter.verify(prefix + "final", digest(body)) == digest(body)


def test_pagination_missing_corrupt_abort_and_deletes(storage):
    adapter, prefix = storage
    for index in range(3):
        upload(adapter, prefix + str(index), b"payload")
    first = adapter.list_page(prefix, limit=2)
    assert first["IsTruncated"] and len(first["Contents"]) == 2
    second = adapter.list_page(prefix, limit=2, continuation=first["NextContinuationToken"])
    keys = [item["Key"] for page in [first, second] for item in page["Contents"]]
    assert len(set(keys)) == len(keys) == 3 and not second["IsTruncated"]
    with pytest.raises(ObjectMissing):
        adapter.verify(prefix + "missing", digest(b"payload"))
    with pytest.raises(ObjectCorrupt):
        adapter.verify(prefix + "0", digest(b"PAYLOAD"))
    upload_id = adapter.initiate(prefix + "aborted")
    assert adapter.find_uploads(prefix + "aborted") == [upload_id]
    adapter.abort(prefix + "aborted", upload_id)
    adapter.abort(prefix + "aborted", upload_id)
    assert adapter.find_uploads(prefix + "aborted") == []
    with pytest.raises(ObjectMissing):
        adapter.parts(prefix + "aborted", upload_id)
    adapter.delete_one(keys[0])
    adapter.delete(keys[1:])
    assert not adapter.list_page(prefix).get("Contents")


def test_declared_size_and_part_layout_rejected_before_completion(storage):
    adapter, prefix = storage
    key = prefix + "bad-layout"
    upload_id = adapter.initiate(key)
    try:
        with httpx.Client(timeout=10, trust_env=False) as http:
            http.put(
                adapter.sign_part(key, upload_id, 2, len(b"out of order")), content=b"out of order"
            ).raise_for_status()
        with pytest.raises(ObjectCorrupt, match="layout"):
            adapter.complete(key, upload_id, len(b"out of order"))
    finally:
        adapter.abort(key, upload_id)


def test_storage_configuration_and_limits():
    for endpoint in ["https://user:secret@example.com", "https://example.com/path", "http://x"]:
        with pytest.raises(ValueError):
            S3Settings(bucket="private", endpoint=endpoint, browser_endpoint=endpoint)
    with pytest.raises(ValueError):
        ObjectDigest("not a digest", 1)
    with pytest.raises(ValueError):
        ObjectDigest("a" * 64, 256 * 1024 * 1024 + 1)


def test_signed_cors_expiry_and_completion_response_loss(storage, monkeypatch):
    from time import sleep

    from botocore.exceptions import EndpointConnectionError

    adapter, prefix = storage
    key = prefix + "cors"
    upload_id = adapter.initiate(key)
    body = b"signed upload with lost completion response"
    try:
        signed = adapter.sign_part(key, upload_id, 1, len(body))
        origin = os.environ.get("VERDANT_STORAGE_TEST_ORIGIN", "http://127.0.0.1:18787")
        with httpx.Client(timeout=10, trust_env=False) as http:
            response = http.options(
                signed,
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "PUT",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == origin
            assert http.put(signed, content=body + b"excess").status_code == 403
            response = http.put(signed, content=body, headers={"Origin": origin})
            response.raise_for_status()
            assert response.headers["access-control-allow-origin"] == origin
            assert "etag" in response.headers.get("access-control-expose-headers", "").lower()
        original = adapter.client.complete_multipart_upload

        def lose_completion(**kwargs):
            original(**kwargs)
            raise EndpointConnectionError(endpoint_url="redacted")

        monkeypatch.setattr(adapter.client, "complete_multipart_upload", lose_completion)
        with pytest.raises(StorageUnavailable):
            adapter.complete(key, upload_id, len(body))
        # An ambiguous completion is reconciled by exact bytes, never an unconditional replay.
        assert adapter.verify(key, digest(body)) == digest(body)
        with pytest.raises(ObjectMissing):
            adapter.parts(key, upload_id)
        expired = adapter.signer.generate_presigned_url(
            "get_object", Params={"Bucket": adapter.settings.bucket, "Key": key}, ExpiresIn=1
        )
        sleep(2)
        with httpx.Client(timeout=10, trust_env=False) as http:
            assert http.get(expired).status_code == 403
    finally:
        adapter.abort(key, upload_id)


def test_final_write_response_loss_reconciles_without_overwrite(storage, monkeypatch):
    from botocore.exceptions import EndpointConnectionError

    adapter, prefix = storage
    body = b"stable final result"
    upload(adapter, prefix + "staging", body)
    original = adapter.client.put_object

    def lose_write(**kwargs):
        original(**kwargs)
        raise EndpointConnectionError(endpoint_url="redacted")

    monkeypatch.setattr(adapter.client, "put_object", lose_write)
    with pytest.raises(StorageUnavailable):
        adapter.promote(prefix + "staging", prefix + "final", digest(body))
    monkeypatch.setattr(adapter.client, "put_object", original)
    assert adapter.verify(prefix + "final", digest(body)) == digest(body)
    assert adapter.promote(prefix + "staging", prefix + "final", digest(body)) == digest(body)


def test_partial_upload_resumes_through_new_adapter_instance(storage):
    adapter, prefix = storage
    key = prefix + "resume"
    body = b"a" * PART_BYTES
    upload_id = adapter.initiate(key)
    try:
        with httpx.Client(timeout=30, trust_env=False) as http:
            http.put(
                adapter.sign_part(key, upload_id, 1, len(body)), content=body
            ).raise_for_status()
        resumed = S3Storage(adapter.settings)
        assert resumed.find_uploads(key) == [upload_id]
        assert resumed.parts(key, upload_id)[0]["Size"] == len(body)
        with httpx.Client(timeout=10, trust_env=False) as http:
            http.put(resumed.sign_part(key, upload_id, 2, 4), content=b"tail").raise_for_status()
        resumed.complete(key, upload_id, len(body) + 4)
        assert resumed.verify(key, digest(body + b"tail")) == digest(body + b"tail")
    finally:
        adapter.abort(key, upload_id)


def test_range_stream_failure_is_safe_and_closes_body():
    class InterruptedBody:
        closed = False

        def read(self, count):
            raise OSError("provider detail must not escape")

        def close(self):
            self.closed = True

    body = InterruptedBody()
    adapter = object.__new__(S3Storage)
    adapter._call = lambda *args, **kwargs: {
        "Body": body,
        "ResponseMetadata": {"HTTPStatusCode": 206},
    }
    with pytest.raises(StorageUnavailable, match="^Object range stream was interrupted$"):
        adapter.read_range("private/key", 0, 5)
    assert body.closed


def test_lost_initiation_response_adopts_exact_staging_upload(storage, monkeypatch):
    from botocore.exceptions import EndpointConnectionError

    adapter, prefix = storage
    key = prefix + "lost-initiation"
    original = adapter.client.create_multipart_upload

    def lose_initiation(**kwargs):
        original(**kwargs)
        raise EndpointConnectionError(endpoint_url="redacted")

    monkeypatch.setattr(adapter.client, "create_multipart_upload", lose_initiation)
    with pytest.raises(StorageUnavailable):
        adapter.initiate(key)
    uploads = adapter.find_uploads(key)
    assert len(uploads) == 1
    upload_id = uploads[0]
    try:
        with httpx.Client(timeout=10, trust_env=False) as http:
            http.put(adapter.sign_part(key, upload_id, 1, 4), content=b"data").raise_for_status()
        adapter.complete(key, upload_id, 4)
        assert adapter.verify(key, digest(b"data")) == digest(b"data")
    finally:
        adapter.abort(key, upload_id)


def test_promotion_python_memory_is_bounded_below_artifact_size(storage, record_property):
    import tracemalloc

    adapter, prefix = storage
    body = b"m" * (2 * PART_BYTES + 1)
    expected = digest(body)
    upload(adapter, prefix + "staging", body)
    # Count adapter/SDK Python allocations only; source-fixture allocation precedes measurement.
    tracemalloc.start()
    try:
        adapter.promote(prefix + "staging", prefix + "final", expected)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    record_property("promotion_python_peak_bytes", peak)
    assert peak < 12 * 1024 * 1024
