import json
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from verdant.domain import Conflict, Forbidden
from verdant.model_inputs import ModelInput, project_model_input
from verdant.model_profiles import (
    ModelAdapter,
    ModelCallError,
    ModelProfileService,
    Profile,
    ProfileCreate,
)
from verdant.process_flow import FlowCreate, FlowStep


def profile(provider="gemini", **patch):
    return Profile(
        id=uuid4(),
        name="Fixture",
        revision=1,
        provider=provider,
        endpoint="not-used-for-routing",
        model="gemini-2.0-flash-001" if provider == "gemini" else "glm-4-20250101",
        identity_kind="exact",
        settings={},
        capabilities=["text", "json"],
        config_digest="a" * 64,
        credential_configured=True,
        **patch,
    )


@pytest.mark.parametrize(
    "content,modality",
    [
        ({"text": "safe", "metadata": {"truth": "secret"}}, "text"),
        (b"x", "image"),
        (b"\xff", "text"),
        (b"\x00", "text"),
        (b"x" * 65537, "text"),
    ],
)
def test_projector_accepts_only_bounded_verified_text_bytes(content, modality):
    with pytest.raises(ValueError):
        project_model_input(content, modality=modality)


def test_content_projector_has_no_metadata_or_interpretation_seam():
    assert project_model_input(b"hello").model_dump() == {"text": "hello"}
    with pytest.raises(TypeError):
        project_model_input(b"hello", metadata={"truth": "held-out"})
    with pytest.raises(ValidationError):
        ModelInput(text="hello", interpretation={"answer": "secret"})


@pytest.mark.parametrize(
    "bad",
    [
        {"settings": {"seed": 1}},
        {"capabilities": ["image"]},
        {"credential_ref": "REAL-API-KEY"},
        {"model": "../escape"},
        {"endpoint": "http://attacker"},
    ],
)
def test_profile_contract_rejects_unsupported_or_unsafe_configuration(bad):
    with pytest.raises(ValidationError):
        ProfileCreate.model_validate(
            dict(
                name="Fixture",
                provider="glm",
                model="glm-4",
                identity_kind="mutable_alias",
                credential_ref="VERDANT_MODEL_SECRET_FIXTURE",
            )
            | bad
        )


def test_flow_contract_rejects_python_and_wrong_step_configuration():
    for bad in [
        {"kind": "python", "name": "unsafe", "code": "exec()"},
        {"kind": "model", "name": "missing", "prompt": "x"},
        {"kind": "preprocess", "name": "bad", "prompt": "x"},
    ]:
        with pytest.raises(ValidationError):
            FlowStep.model_validate(bad)
    with pytest.raises(ValidationError):
        FlowCreate(name="Dup", steps=[FlowStep(name="x", kind="preprocess")] * 2)


@pytest.mark.parametrize("provider", ["gemini", "glm"])
def test_normalized_protocol_fixed_origin_missing_usage_and_no_secret_errors(provider, monkeypatch):
    secret = "fixture-never-log-me"
    monkeypatch.setenv("VERDANT_MODEL_SECRET_FIXTURE", secret)
    seen = []

    def handler(request):
        seen.append(request)
        body = json.loads(request.content)
        assert body.get("stream", False) is False
        if provider == "gemini":
            assert request.url.host == "generativelanguage.googleapis.com"
            assert body["contents"] == [{"role": "user", "parts": [{"text": "only content"}]}]
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {"finishReason": "STOP", "content": {"parts": [{"text": "answer"}]}}
                    ],
                    "modelVersion": "exact-reported",
                },
            )
        assert request.url.host == "open.bigmodel.cn"
        assert body["messages"][1] == {"role": "user", "content": "only content"}
        return httpx.Response(
            200,
            json={
                "id": "call-1",
                "choices": [{"finish_reason": "stop", "message": {"content": "answer"}}],
            },
        )

    result = ModelAdapter(transport=httpx.MockTransport(handler)).generate(
        profile(provider),
        "VERDANT_MODEL_SECRET_FIXTURE",
        project_model_input(b"only content"),
        prompt="Instruction",
        output_contract="text",
    )
    assert result.text == "answer"
    assert result.input_tokens is result.output_tokens is result.cost is None
    assert len(seen) == 1


def test_429_is_bounded_and_read_timeout_is_never_replayed(monkeypatch):
    monkeypatch.setenv("VERDANT_MODEL_SECRET_FIXTURE", "synthetic")
    waits, calls = [], []

    def rate_limit(request):
        calls.append(1)
        return httpx.Response(
            429, headers={"retry-after": "500"}, json={"private": "never surface"}
        )

    adapter = ModelAdapter(transport=httpx.MockTransport(rate_limit), sleep=waits.append)
    with pytest.raises(ModelCallError, match="rate_limited") as error:
        adapter.generate(
            profile(),
            "VERDANT_MODEL_SECRET_FIXTURE",
            ModelInput(text="private"),
            prompt="private",
            output_contract="text",
        )
    assert not error.value.uncertain and len(calls) == 3 and waits == [4, 4]
    calls.clear()

    def timeout(request):
        calls.append(1)
        raise httpx.ReadTimeout("secret prompt transport")

    with pytest.raises(ModelCallError, match="provider_outcome_unknown") as error:
        ModelAdapter(transport=httpx.MockTransport(timeout)).generate(
            profile(),
            "VERDANT_MODEL_SECRET_FIXTURE",
            ModelInput(text="private"),
            prompt="private",
            output_contract="text",
        )
    assert error.value.uncertain and len(calls) == 1
    assert "secret" not in str(error.value)


def test_profile_versions_digest_alias_and_backend_only_reference(
    registry, sessions, principal_factory, monkeypatch
):
    _, actor, ns = registry
    service = ModelProfileService(sessions)
    command = ProfileCreate(
        name="Model",
        provider="glm",
        model="glm-4",
        identity_kind="mutable_alias",
        credential_ref="VERDANT_MODEL_SECRET_FIXTURE",
    )
    first = service.create(actor, ns, command)
    assert not first.credential_configured
    assert "credential_ref" not in first.model_dump()
    assert first.identity_kind == "mutable_alias"
    monkeypatch.setenv(command.credential_ref, "synthetic")
    second = service.create(
        actor, ns, command.model_copy(update={"expected_revision": 1, "model": "glm-4-plus"})
    )
    assert second.credential_configured and first.config_digest != second.config_digest
    assert service.get(actor, ns, first.id).model == "glm-4"
    with pytest.raises(Conflict):
        service.create(actor, ns, command)
    with pytest.raises(Forbidden):
        service.list(principal_factory(), ns)


@pytest.mark.parametrize(
    "raw",
    [
        {"choices": [{"finish_reason": "stop", "message": {"content": "private\x00text"}}]},
        {
            "id": "x" * 501,
            "choices": [{"finish_reason": "stop", "message": {"content": "private"}}],
        },
        {"choices": [{"finish_reason": "length", "message": {"content": "private"}}]},
        {
            "choices": [{"finish_reason": "stop", "message": {"content": "private"}}],
            "usage": {"prompt_tokens": -1},
        },
        {"choices": []},
    ],
)
def test_malformed_accepted_output_is_safe_uncertain_error(raw, monkeypatch):
    monkeypatch.setenv("VERDANT_MODEL_SECRET_FIXTURE", "synthetic")
    adapter = ModelAdapter(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=raw)))
    with pytest.raises(ModelCallError) as caught:
        adapter.generate(
            profile("glm"),
            "VERDANT_MODEL_SECRET_FIXTURE",
            ModelInput(text="private"),
            prompt="private",
            output_contract="text",
        )
    assert caught.value.uncertain
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("headers", [{"content-encoding": "gzip"}, {"content-length": "262145"}])
def test_rejects_compression_and_oversized_declaration_before_reading(headers, monkeypatch):
    monkeypatch.setenv("VERDANT_MODEL_SECRET_FIXTURE", "synthetic")

    class UnreadBody(httpx.SyncByteStream):
        read = False

        def __iter__(self):
            self.read = True
            raise AssertionError("Rejected response body must never be read/decompressed")
            yield b""

    body = UnreadBody()

    def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers=headers, stream=body)

    with pytest.raises(ModelCallError) as caught:
        ModelAdapter(transport=httpx.MockTransport(handler)).generate(
            profile("glm"),
            "VERDANT_MODEL_SECRET_FIXTURE",
            ModelInput(text="private"),
            prompt="private",
            output_contract="text",
        )
    assert caught.value.uncertain and not body.read


def test_process_openapi_has_typed_pages_and_detail_for_sdk_generation():
    from fastapi import FastAPI

    from verdant.api import Actor
    from verdant.process_routes import install_process_routes

    app = FastAPI()
    install_process_routes(app, None, Actor)
    assert all(
        route.response_model is not None for route in app.routes if hasattr(route, "response_model")
    )
    schema = app.openapi()
    for name in (
        "ModelProfilePage",
        "FlowPage",
        "ProcessDetail",
        "ProcessOutput",
        "FlowDefinition",
        "PinnedFlowStep",
    ):
        assert name in schema["components"]["schemas"]
    assert "credential_ref" not in schema["components"]["schemas"]["Profile"]["properties"]
