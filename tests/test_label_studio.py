import hashlib
import json as jsonlib

import httpx
import pytest

from verdant.label_studio import (
    MAX_RESPONSE_BYTES,
    LabelStudioClient,
    LabelStudioConflict,
    LabelStudioSettings,
    LabelStudioUnavailable,
)

SCHEMA = '<View><Text name="text" value="$text"/></View>'
DIGEST = hashlib.sha256(SCHEMA.encode()).hexdigest()
SETTINGS = LabelStudioSettings(
    browser_origin="https://labels.example.org",
    machine_origin="https://machine.example.org",
    image="registry.example.org/label-studio@sha256:" + "a" * 64,
)


def response(status, *, json=None, headers=None, text=None, content=None):
    body = jsonlib.dumps(json).encode() if json is not None else content
    if text is not None:
        body = text.encode()
    return httpx.Response(status, headers=headers, stream=httpx.ByteStream(body or b""))


def client(handler):
    return LabelStudioClient(
        SETTINGS, "test-only-service-token", transport=httpx.MockTransport(handler)
    )


def test_native_snapshot_uses_machine_token_but_preserves_actual_author():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Token test-only-service-token"
        assert request.headers["Accept-Encoding"] == "identity"
        if request.url.path == "/api/projects/7/":
            return response(
                200,
                json={
                    "id": 7,
                    "label_config": SCHEMA,
                    "evaluate_predictions_automatically": False,
                    "show_collab_predictions": False,
                },
            )
        assert request.url.path == "/api/tasks/8/annotations/"
        return response(
            200,
            json=[
                {
                    "id": 9,
                    "task": 8,
                    "completed_by": {"id": 42, "email": "untrusted-display@example.org"},
                    "result": [],
                    "was_cancelled": False,
                    "created_username": "Not an identity binding",
                }
            ],
        )

    adapter = client(handler)
    try:
        annotations = adapter.task_annotations(7, 8, DIGEST)
        assert annotations[0].completed_by == 42
        assert len(requests) == 3
        assert SETTINGS.task_url(7, 8) == "https://labels.example.org/projects/7/data?task=8"
        assert all(request.url.host == "machine.example.org" for request in requests)
    finally:
        adapter.close()


@pytest.mark.parametrize("status", [302, 401, 403, 500])
def test_provider_errors_do_not_redirect_or_expose_private_body(status):
    adapter = client(
        lambda request: response(
            status, headers={"Location": "https://untrusted.example"}, text="PRIVATE PROVIDER BODY"
        )
    )
    try:
        with pytest.raises(LabelStudioUnavailable) as error:
            adapter.project_schema(7)
        assert "PRIVATE" not in str(error.value)
    finally:
        adapter.close()


@pytest.mark.parametrize(
    "headers,body",
    [
        ({}, b"x" * (MAX_RESPONSE_BYTES + 1)),
        ({"Content-Encoding": "gzip"}, b"compressed"),
        ({}, b"null"),
    ],
    ids=["oversize", "encoded", "wrong-shape"],
)
def test_response_bounds_and_encoding(headers, body):
    adapter = client(lambda request: response(200, headers=headers, content=body))
    try:
        with pytest.raises(LabelStudioUnavailable):
            adapter.project_schema(7)
    finally:
        adapter.close()


@pytest.mark.parametrize("change", ["schema", "task", "author", "duplicate"])
def test_changed_schema_and_untrusted_native_identity_are_rejected(change):
    def handler(request):
        if "/projects/" in request.url.path:
            return response(
                200, json={"id": 7, "label_config": SCHEMA + (" " if change == "schema" else "")}
            )
        annotation = {"id": 9, "task": 8, "completed_by": 42, "result": [], "was_cancelled": False}
        if change == "author":
            annotation["completed_by"] = {"email": "claimed@example.org"}
        return response(
            200, json=[annotation, annotation] if change == "duplicate" else [annotation]
        )

    adapter = client(handler)
    try:
        with pytest.raises(LabelStudioConflict):
            adapter.task_annotations(7, 8, DIGEST)
    finally:
        adapter.close()


@pytest.mark.parametrize("value", [True, -1, 0, "8", "../8"])
def test_native_ids_cannot_construct_arbitrary_routes(value):
    with pytest.raises(ValueError):
        SETTINGS.task_url(7, value)


def test_profile_requires_https_separate_origins_and_image_digest():
    with pytest.raises(ValueError):
        LabelStudioSettings("http://labels.example.org", SETTINGS.machine_origin, SETTINGS.image)
    with pytest.raises(ValueError):
        LabelStudioSettings(SETTINGS.browser_origin, SETTINGS.browser_origin, SETTINGS.image)
    with pytest.raises(ValueError):
        LabelStudioSettings(SETTINGS.browser_origin, SETTINGS.machine_origin, "label-studio:latest")


@pytest.mark.parametrize(
    "origin",
    [
        "https://labels.example.org/path",
        "https://user@labels.example.org",
        "https://labels.example.org:99999",
        "https://labels.example.org\\evil",
        "https://labels.example.org?redirect=elsewhere",
        "https://labels.example.org/#fragment",
    ],
)
def test_noncanonical_origins_are_rejected(origin):
    with pytest.raises(ValueError):
        LabelStudioSettings(origin, SETTINGS.machine_origin, SETTINGS.image)


@pytest.mark.parametrize(
    "flag,value",
    [
        ("evaluate_predictions_automatically", True),
        ("show_collab_predictions", True),
        ("evaluate_predictions_automatically", None),
        ("show_collab_predictions", 0),
    ],
)
def test_task_fetch_cannot_trigger_native_prediction_calls(flag, value):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        assert request.url.path == "/api/projects/7/"
        project = {
            "id": 7,
            "label_config": SCHEMA,
            "evaluate_predictions_automatically": False,
            "show_collab_predictions": False,
        }
        project[flag] = value
        return response(200, json=project)

    adapter = client(handler)
    try:
        with pytest.raises(LabelStudioConflict, match="predictions"):
            adapter.task_annotations(7, 8, DIGEST)
        assert requests == ["/api/projects/7/"]
    finally:
        adapter.close()


@pytest.mark.parametrize("change", [None, "next", "previous", "count"])
def test_paginated_annotations_require_a_complete_bounded_page(change):
    def handler(request):
        if request.url.path == "/api/projects/7/":
            return response(
                200,
                json={
                    "id": 7,
                    "label_config": SCHEMA,
                    "evaluate_predictions_automatically": False,
                    "show_collab_predictions": False,
                },
            )
        page = {"count": 0, "next": None, "previous": None, "results": []}
        if change in {"next", "previous"}:
            page[change] = "https://untrusted.example/continuation"
        elif change == "count":
            page["count"] = 1000
        return response(200, json=page)

    adapter = client(handler)
    try:
        if change is None:
            assert adapter.task_annotations(7, 8, DIGEST) == []
        else:
            with pytest.raises(LabelStudioConflict):
                adapter.task_annotations(7, 8, DIGEST)
    finally:
        adapter.close()


def test_compressed_response_is_rejected_before_read_or_decompression():
    class NeverRead(httpx.SyncByteStream):
        def __iter__(self):
            raise AssertionError("Compressed body must never be consumed")
            yield b""

    adapter = client(
        lambda request: httpx.Response(
            200, headers={"Content-Encoding": "gzip"}, stream=NeverRead()
        )
    )
    try:
        with pytest.raises(LabelStudioUnavailable, match="encoding"):
            adapter.project_schema(7)
    finally:
        adapter.close()


@pytest.mark.parametrize(
    "modifier", [True, 0, -1, "42", {"id": True}, {"email": "not-an-identity@example.test"}]
)
def test_native_modifier_identity_is_strict_and_never_email_linked(modifier):
    def handler(request):
        if request.url.path == "/api/projects/7/":
            return response(
                200,
                json={
                    "id": 7,
                    "label_config": SCHEMA,
                    "evaluate_predictions_automatically": False,
                    "show_collab_predictions": False,
                },
            )
        return response(
            200,
            json=[
                {
                    "id": 9,
                    "task": 8,
                    "completed_by": 42,
                    "updated_by": modifier,
                    "result": [],
                    "was_cancelled": False,
                }
            ],
        )

    adapter = client(handler)
    try:
        with pytest.raises(LabelStudioConflict):
            adapter.task_annotations(7, 8, DIGEST)
    finally:
        adapter.close()
