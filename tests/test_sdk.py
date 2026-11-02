import hashlib
import io
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from verdant.contracts import DatasetCreate, NamespacePage
from verdant.sdk import Client, SDKError

VERSION = {"X-Verdant-Contract-Version": "1"}


def reply(data, status=200, **kwargs):
    return httpx.Response(status, json=data, headers=VERSION, **kwargs)


def test_generated_methods_are_typed_and_page_without_repeating_cursor():
    first, second = uuid4(), uuid4()
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer current-token"
        if request.url.params.get("after") is None:
            return reply(
                {
                    "items": [{"id": str(first), "name": "a", "access": "write"}],
                    "next_after": str(first),
                }
            )
        assert request.url.params["after"] == str(first)
        return reply(
            {"items": [{"id": str(second), "name": "b", "access": "read"}], "next_after": None}
        )

    with Client(
        "https://platform.test", lambda: "current-token", transport=httpx.MockTransport(handler)
    ) as sdk:
        pages = list(sdk.iter_pages(sdk.list_namespaces, limit=1))
    assert all(isinstance(page, NamespacePage) for page in pages)
    assert [item.id for page in pages for item in page.items] == [first, second]
    assert len(requests) == 2


def test_read_retries_bounded_but_lost_command_response_never_repeats(monkeypatch):
    monkeypatch.setattr("verdant.sdk.transport.time.sleep", lambda _: None)
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "GET" and len(calls) == 3:
            return reply({"items": [], "next_after": None})
        if request.method == "POST":
            raise httpx.ReadTimeout("private signed URL/provider body must not appear")
        return reply({"detail": "Temporarily unavailable"}, 503)

    with Client("https://platform.test", "token", transport=httpx.MockTransport(handler)) as sdk:
        assert sdk.list_namespaces().items == []
        with pytest.raises(SDKError, match="outcome may be unknown") as error:
            sdk.create_dataset(
                namespace_id=uuid4(), command=DatasetCreate(slug="new", name="New", modality="text")
            )
        assert "private" not in str(error.value)
    assert calls == ["GET", "GET", "GET", "POST"]


def test_structured_errors_version_and_response_schema_fail_closed():
    def call(response):
        with Client(
            "https://platform.test", "token", transport=httpx.MockTransport(lambda _: response)
        ) as sdk:
            return sdk.list_namespaces()

    with pytest.raises(SDKError, match="Namespace access denied") as error:
        call(reply({"detail": "Namespace access denied"}, 403))
    assert error.value.status == 403 and not error.value.retryable
    with pytest.raises(SDKError, match="contract version"):
        call(httpx.Response(200, json={"items": []}))
    with pytest.raises(SDKError, match="typed contract"):
        call(reply({"items": "invalid"}))


def descriptor(body):
    return SimpleNamespace(
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
        download_path="/api/v1/artifact/download",
    )


def test_redirect_verifies_bytes_and_never_forwards_auth_or_cookies():
    body = b"immutable payload"
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.host == "platform.test":
            return httpx.Response(
                307,
                headers=VERSION
                | {
                    "location": "https://objects.test/placement?signature=ephemeral",
                    "set-cookie": "platform=private; Secure; Path=/",
                },
            )
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        return httpx.Response(200, content=body)

    with Client(
        "https://platform.test",
        "secret",
        storage_origins=("https://objects.test",),
        transport=httpx.MockTransport(handler),
    ) as sdk:
        target = io.BytesIO()
        assert sdk.download_verified(descriptor(body), target) == len(body)
        assert target.getvalue() == body
    assert len(seen) == 2


@pytest.mark.parametrize("actual", [b"wrong bytes", b"", b"expected plus surplus"])
def test_corrupt_or_truncated_download_never_writes_destination(actual):
    with Client(
        "https://platform.test",
        "token",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=actual, headers=VERSION)
        ),
    ) as sdk:
        target = io.BytesIO(b"existing content")
        target.seek(0, 2)
        with pytest.raises(SDKError, match="immutable|digest"):
            sdk.download_verified(descriptor(b"expected"), target)
        assert target.getvalue() == b"existing content"


def test_unconfigured_redirect_is_denied_before_external_network():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            307, headers=VERSION | {"location": "https://attacker.test/object?private=secret"}
        )

    with Client("https://platform.test", "token", transport=httpx.MockTransport(handler)) as sdk:
        with pytest.raises(SDKError, match="not configured") as error:
            sdk.download_verified(descriptor(b"expected"), io.BytesIO())
        assert "secret" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "origin",
    [
        "http://remote.test",
        "https://u:p@api.test",
        "https://api.test/private",
        "https://api.test?token=x",
    ],
)
def test_configuration_rejects_untrusted_origins(origin):
    with pytest.raises(ValueError):
        Client(origin, "token", allow_insecure_http=True)


def test_cursor_cycle_cannot_loop_forever():
    cursor = uuid4()
    with Client(
        "https://platform.test",
        "token",
        transport=httpx.MockTransport(lambda _: reply({"items": [], "next_after": str(cursor)})),
    ) as sdk:
        with pytest.raises(SDKError, match="repeated a cursor"):
            list(sdk.iter_pages(sdk.list_namespaces))


@pytest.mark.parametrize(
    "mutation", [{"schema": "future.manifest.v2"}, {"dataset_id": str(uuid4())}]
)
def test_manifest_schema_and_logical_identity_verified_after_digest(mutation, monkeypatch):
    import json

    ns, dataset, candidate, generation, artifact = [uuid4() for _ in range(5)]
    record = {
        "schema": "verdant.manifest.v1",
        "namespace_id": str(ns),
        "dataset_id": str(dataset),
        "candidate_id": str(candidate),
        "generation_id": str(generation),
        "source_revision": 0,
        "policy": {},
        "validation_report": {},
        "members": [],
    } | mutation
    body = json.dumps(record).encode()
    desc = descriptor(body)
    desc.artifact_id = artifact
    version = SimpleNamespace(
        manifest_artifact_id=artifact,
        manifest_digest=desc.sha256,
        manifest_size=desc.size_bytes,
        candidate_id=candidate,
        generation_id=generation,
    )
    with Client(
        "https://platform.test",
        "token",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body, headers=VERSION)),
    ) as sdk:
        monkeypatch.setattr(sdk, "get_version", lambda **_: version)
        monkeypatch.setattr(sdk, "version_manifest", lambda **_: desc)
        with pytest.raises(SDKError, match="schema|identity"):
            sdk.verified_manifest(namespace_id=ns, dataset_id=dataset, version_id=uuid4())


def test_waiter_stops_on_ambiguous_receipt_and_never_mutates(monkeypatch):
    state = SimpleNamespace(
        needs_reconciliation=True, operation=SimpleNamespace(status="reconciliation_needed")
    )
    with Client("https://platform.test", "token") as sdk:
        monkeypatch.setattr(sdk, "operation_detail", lambda **_: state)
        assert sdk.wait_operation(namespace_id=uuid4(), operation_id=uuid4()) is state
        state.needs_reconciliation = False
        state.operation.status = "running"
        with pytest.raises(SDKError, match="without mutation"):
            sdk.wait_operation(namespace_id=uuid4(), operation_id=uuid4(), polls=2, interval=0)


@pytest.mark.parametrize("objects", [False, True])
def test_compressed_responses_rejected_before_any_decoder_read(objects):
    import gzip

    reads = []

    class Compressed(httpx.SyncByteStream):
        def __iter__(self):
            reads.append(True)
            yield gzip.compress(b"a" * (8 * 1024 * 1024))

    def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        if objects and request.url.host == "platform.test":
            return httpx.Response(
                307, headers=VERSION | {"location": "https://objects.test/key?signature=private"}
            )
        return httpx.Response(
            200, headers=VERSION | {"Content-Encoding": "gzip"}, stream=Compressed()
        )

    with Client(
        "https://platform.test",
        "token",
        storage_origins=("https://objects.test",),
        transport=httpx.MockTransport(handler),
    ) as sdk:
        with pytest.raises(SDKError, match="identity encoding"):
            if objects:
                sdk.download_verified(descriptor(b"payload"), io.BytesIO())
            else:
                sdk.list_namespaces()
    assert reads == []


@pytest.mark.parametrize("artifact", [False, True])
def test_slow_raw_fragments_cannot_hide_the_request_deadline(artifact, monkeypatch):
    clock, reads = [0.0], []
    monkeypatch.setattr("verdant.sdk.transport.time.monotonic", lambda: clock[0])

    class Slow(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(100):
                clock[0] += 4
                reads.append(True)
                yield b" "

    with Client(
        "https://platform.test",
        "token",
        timeout=5,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers=VERSION, stream=Slow())
        ),
    ) as sdk:
        target = io.BytesIO(b"retained")
        with pytest.raises(SDKError, match="deadline exceeded"):
            if artifact:
                sdk.download_verified(descriptor(b" " * 100), target)
            else:
                sdk.list_namespaces()
        assert len(reads) == 2
        assert target.getvalue() == b"retained"


@pytest.mark.parametrize("status", [429, 503])
def test_command_http_error_never_advertises_safe_automatic_retry(status):
    calls = []

    def handler(request):
        calls.append(request)
        return reply({"detail": "Command may require reconciliation"}, status)

    with Client("https://platform.test", "token", transport=httpx.MockTransport(handler)) as sdk:
        with pytest.raises(SDKError) as error:
            sdk.create_dataset(
                namespace_id=uuid4(),
                command=DatasetCreate(slug="pending", name="Pending", modality="text"),
            )
        assert not error.value.retryable
        assert error.value.status == status
    assert len(calls) == 1
