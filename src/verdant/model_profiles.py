"""Immutable provider profiles and bounded, fixed-origin HTTP protocol adapters."""

import hashlib
import json
import os
import time
from typing import Literal
from uuid import UUID

import httpx
from pydantic import Field, model_validator
from sqlalchemy import func, select

from verdant.contracts import Contract, Digest
from verdant.domain import Conflict, NotFound
from verdant.identity import current_principal, require_current_access
from verdant.model_inputs import ModelInput
from verdant.persistence import AuditRow, ModelProfileRow, NamespaceRow
from verdant.telemetry import observed

ORIGINS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "glm": "https://open.bigmodel.cn/api/paas/v4",
}


def config_digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


class ModelSettings(Contract):
    temperature: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    top_p: float = Field(default=1, gt=0, le=1, allow_inf_nan=False)
    max_output_tokens: int = Field(default=256, ge=1, le=4096, strict=True)


class ProfileCreate(Contract):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9 _.-]*$")
    expected_revision: int = Field(default=0, ge=0, strict=True)
    provider: Literal["gemini", "glm"]
    model: str = Field(min_length=1, max_length=150, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    identity_kind: Literal["exact", "mutable_alias"]
    credential_ref: str = Field(pattern=r"^VERDANT_MODEL_SECRET_[A-Z0-9_]{1,80}$")
    settings: ModelSettings = Field(default_factory=ModelSettings)
    capabilities: list[Literal["text", "json"]] = Field(default_factory=lambda: ["text"])

    @model_validator(mode="after")
    def supported(self):
        if "text" not in self.capabilities or len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("Text capability is required; duplicate capabilities are invalid")
        return self


class Profile(Contract):
    id: UUID
    name: str
    revision: int
    provider: Literal["gemini", "glm"]
    endpoint: str
    model: str
    identity_kind: Literal["exact", "mutable_alias"]
    settings: ModelSettings
    capabilities: list[Literal["text", "json"]]
    config_digest: Digest
    credential_status: Literal["worker_unverified"] = "worker_unverified"


class ModelProfilePage(Contract):
    items: list[Profile]
    next_after: UUID | None = None


class ModelResult(Contract):
    text: str = Field(max_length=65536)
    provider_reference: str | None = Field(default=None, max_length=500)
    resolved_model: str | None = Field(default=None, max_length=150)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    # Providers do not supply trustworthy billing here; absence is never zero.
    cost: None = None

    @model_validator(mode="after")
    def safe_output(self):
        ModelInput(text=self.text)
        for value in (self.provider_reference, self.resolved_model):
            if value is not None and ("\x00" in value or len(value.encode()) > 500):
                raise ValueError("Invalid normalized provider identity")
        return self


class ModelCallError(Exception):
    def __init__(self, code: str, *, uncertain=False):
        super().__init__(code)
        self.code, self.uncertain = code, uncertain


class ModelProfileService:
    def __init__(self, sessions):
        self.sessions = sessions

    @staticmethod
    def _view(row):
        definition = row.definition
        return Profile(
            id=row.id,
            name=row.name,
            revision=row.revision,
            endpoint=ORIGINS[definition["provider"]],
            config_digest=row.config_digest,
            credential_status="worker_unverified",
            **definition,
        )

    @observed("models")
    def create(self, actor, namespace_id, command: ProfileCreate):
        command = ProfileCreate.model_validate(command.model_dump())
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id, write=True)
            session.scalar(
                select(NamespaceRow).where(NamespaceRow.id == namespace_id).with_for_update()
            )
            require_current_access(session, actor.id, namespace_id, write=True)
            latest = (
                session.scalar(
                    select(func.max(ModelProfileRow.revision)).where(
                        ModelProfileRow.namespace_id == namespace_id,
                        ModelProfileRow.name == command.name,
                    )
                )
                or 0
            )
            if latest != command.expected_revision:
                raise Conflict("Model profile revision changed")
            definition = command.model_dump(
                mode="json", exclude={"name", "expected_revision", "credential_ref"}
            )
            row = ModelProfileRow(
                namespace_id=namespace_id,
                name=command.name,
                revision=latest + 1,
                definition=definition,
                credential_ref=command.credential_ref,
                config_digest=config_digest(
                    definition
                    | {
                        "credential_ref": command.credential_ref,
                        "endpoint": ORIGINS[command.provider],
                    }
                ),
                creator_id=actor.id,
            )
            session.add(row)
            session.flush()
            session.add(
                AuditRow(
                    namespace_id=namespace_id,
                    actor_id=actor.id,
                    aggregate_id=row.id,
                    action="model_profile.created",
                    schema_version=1,
                    payload={"transport": actor.transport, "config_digest": row.config_digest},
                )
            )
            return self._view(row)

    def list(self, actor, namespace_id, after=None, limit=50):
        if not 1 <= limit <= 100:
            raise ValueError("Profile page must be between1 and100")
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id)
            query = select(ModelProfileRow).where(ModelProfileRow.namespace_id == namespace_id)
            if after:
                query = query.where(ModelProfileRow.id > after)
            rows = session.scalars(query.order_by(ModelProfileRow.id).limit(limit + 1)).all()
            return {
                "items": [self._view(row) for row in rows[:limit]],
                "next_after": rows[limit - 1].id if len(rows) > limit else None,
            }

    def get(self, actor, namespace_id, profile_id):
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id)
            row = session.get(ModelProfileRow, profile_id)
            if row is None or row.namespace_id != namespace_id:
                raise NotFound("Model profile not found")
            return self._view(row)


class ModelAdapter:
    """Only confirmed rejected calls may back off. Accepted/ambiguous calls never replay.

    One effect dispatch encompasses at most three rejected HTTP attempts. The caller
    persists unknown BEFORE entering this method and accepts the result atomically.
    No exception includes provider payload, prompts, credentials, or transport URL.
    """

    def __init__(self, *, transport=None, sleep=time.sleep):
        self.transport, self.sleep = transport, sleep

    @observed("models")
    def generate(
        self,
        profile: Profile,
        credential_ref: str,
        value: ModelInput,
        *,
        prompt: str,
        output_contract: str,
        deadline: float | None = None,
    ) -> ModelResult:
        if output_contract not in profile.capabilities:
            raise ModelCallError("unsupported_capability")
        secret = os.environ.get(credential_ref)
        if not secret:
            raise ModelCallError("credential_unconfigured")
        deadline = min(deadline or float("inf"), time.monotonic() + 30)
        value = ModelInput.model_validate(value.model_dump())
        settings = profile.settings
        if profile.provider == "gemini":
            url = f"{ORIGINS['gemini']}/models/{profile.model}:generateContent"
            headers = {"x-goog-api-key": secret, "Accept-Encoding": "identity"}
            body = {
                "contents": [{"role": "user", "parts": [{"text": value.text}]}],
                "systemInstruction": {"parts": [{"text": prompt}]},
                "generationConfig": {
                    "temperature": settings.temperature,
                    "topP": settings.top_p,
                    "maxOutputTokens": settings.max_output_tokens,
                },
            }
            if output_contract == "json":
                body["generationConfig"]["responseMimeType"] = "application/json"
        else:
            url = ORIGINS["glm"] + "/chat/completions"
            headers = {"Authorization": "Bearer " + secret, "Accept-Encoding": "identity"}
            body = {
                "model": profile.model,
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": value.text},
                ],
                "temperature": settings.temperature,
                "top_p": settings.top_p,
                "max_tokens": settings.max_output_tokens,
                "stream": False,
            }
            if output_contract == "json":
                body["response_format"] = {"type": "json_object"}
        with httpx.Client(
            timeout=httpx.Timeout(5, connect=5),
            trust_env=False,
            follow_redirects=False,
            transport=self.transport,
        ) as client:
            for attempt in range(3):
                if time.monotonic() >= deadline:
                    raise ModelCallError("provider_time_budget")
                try:
                    with client.stream("POST", url, headers=headers, json=body) as response:
                        status = response.status_code
                        if status == 429:
                            if attempt < 2:
                                delay = min(2**attempt, 4)
                                retry_after = response.headers.get("retry-after", "")
                                if retry_after.isdecimal():
                                    delay = min(max(delay, int(retry_after)), 4)
                                self.sleep(delay)
                                continue
                            raise ModelCallError("rate_limited")
                        if status in {400, 401, 403, 404, 405, 413, 415, 422}:
                            raise ModelCallError("provider_rejected")
                        if status != 200:
                            raise ModelCallError("provider_outcome_unknown", uncertain=True)
                        encoding = (
                            response.headers.get("content-encoding", "identity").strip().lower()
                        )
                        if encoding != "identity":
                            raise ModelCallError("provider_compressed_response", uncertain=True)
                        length = response.headers.get("content-length")
                        if length is not None and (not length.isdecimal() or int(length) > 262144):
                            raise ModelCallError("provider_response_too_large", uncertain=True)
                        chunks, size = [], 0
                        for chunk in response.iter_raw():
                            if time.monotonic() >= deadline:
                                raise ModelCallError("provider_time_budget", uncertain=True)
                            size += len(chunk)
                            if size > 262144:
                                raise ModelCallError("provider_response_too_large", uncertain=True)
                            chunks.append(chunk)
                        raw = json.loads(b"".join(chunks))
                    if profile.provider == "gemini":
                        candidate = raw["candidates"][0]
                        if candidate.get("finishReason") != "STOP":
                            raise ModelCallError("provider_output_incomplete", uncertain=True)
                        parts = candidate["content"]["parts"]
                        if any(
                            set(part) - {"text", "thought", "thoughtSignature"} for part in parts
                        ):
                            raise ModelCallError("provider_output_unsupported", uncertain=True)
                        text = "".join(
                            part["text"]
                            for part in parts
                            if not part.get("thought") and "text" in part
                        )
                        if not text:
                            raise ModelCallError("provider_output_missing", uncertain=True)
                        usage = raw.get("usageMetadata") or {}
                        result = ModelResult(
                            text=text,
                            provider_reference=raw.get("responseId"),
                            resolved_model=raw.get("modelVersion"),
                            input_tokens=usage.get("promptTokenCount"),
                            output_tokens=usage.get("candidatesTokenCount"),
                        )
                    else:
                        choice = raw["choices"][0]
                        if choice.get("finish_reason") != "stop":
                            raise ModelCallError("provider_output_incomplete", uncertain=True)
                        usage = raw.get("usage") or {}
                        result = ModelResult(
                            text=choice["message"]["content"],
                            provider_reference=raw.get("id"),
                            resolved_model=raw.get("model"),
                            input_tokens=usage.get("prompt_tokens"),
                            output_tokens=usage.get("completion_tokens"),
                        )
                    if len(result.text.encode()) > 65536:
                        raise ModelCallError("provider_response_too_large", uncertain=True)
                    if output_contract == "json":
                        json.loads(result.text)
                    return result
                except ModelCallError:
                    raise
                except (
                    httpx.HTTPError,
                    httpx.StreamError,
                    ValueError,
                    TypeError,
                    KeyError,
                    IndexError,
                    AttributeError,
                    RecursionError,
                    OverflowError,
                ):
                    raise ModelCallError("provider_outcome_unknown", uncertain=True) from None
        raise AssertionError("Bounded provider loop exhausted")
