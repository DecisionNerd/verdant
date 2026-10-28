"""Versioned, bounded API and shared-service contracts."""

import hashlib
import json
import math
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

CONTRACT_VERSION = 1
EVENT_VERSION = 1
MANIFEST_VERSION = 1  # Reserved encoding version; publication is not implemented yet.
SCHEMA_REVISION = "0016_quality"

Slug = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Revision = Annotated[int, Field(ge=0, le=9223372036854775807, strict=True)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="after")
    def postgres_values(self):
        def check(value):
            if isinstance(value, str) and "\x00" in value:
                raise ValueError("NUL characters are not supported")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Non-finite JSON numbers are not supported")
            if isinstance(value, dict):
                for key, item in value.items():
                    check(key)
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)

        check(self.model_dump())
        return self


class Namespace(Contract):
    id: UUID
    name: str
    access: str


class NamespacePage(Contract):
    items: list[Namespace]
    next_after: UUID | None = None


class DatasetCreate(Contract):
    slug: Slug
    name: str = Field(min_length=1, max_length=200)
    modality: str = Field(min_length=1, max_length=50)


class DatasetUpdate(DatasetCreate):
    expected_revision: Revision


class Dataset(Contract):
    id: UUID
    namespace_id: UUID
    owner_id: UUID
    revision: int = 0
    slug: str
    name: str
    modality: str
    draft_id: UUID
    created_at: datetime


class DatasetPage(Contract):
    items: list[Dataset]
    total: int
    next_after: UUID | None = None


class SampleContent(Contract):
    sha256: Digest
    object_key: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0, le=9223372036854775807, strict=True)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded_metadata(self):
        if (
            len(json.dumps(self.metadata, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            > 65536
        ):
            raise ValueError("Sample metadata exceeds 64 KiB")
        return self


class SampleCreate(SampleContent):
    source_key: str = Field(min_length=1, max_length=500)
    modality: str = Field(min_length=1, max_length=50)


class SampleRevise(SampleContent):
    expected_revision: Revision


class SampleRevision(Contract):
    id: UUID
    namespace_id: UUID
    sample_id: UUID
    revision: int
    sha256: str
    object_key: str
    size_bytes: int
    metadata: dict[str, JsonValue]
    created_at: datetime


class SampleSummary(Contract):
    sample_id: UUID
    revision_id: UUID
    revision: int
    modality: str
    sha256: str
    size_bytes: int


class SamplePage(Contract):
    items: list[SampleSummary]
    next_after: UUID | None = None


class InlineEvidence(Contract):
    """Actual bounded immutable content, rather than an unresolvable external identifier."""

    format: str = Field(min_length=1, max_length=200)
    payload: dict[str, JsonValue]
    sha256: Digest | None = None

    @model_validator(mode="after")
    def content_identity(self):
        encoded = json.dumps(
            {"format": self.format, "payload": self.payload},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        if len(encoded) > 65536:
            raise ValueError("Inline evidence exceeds 64 KiB")
        digest = hashlib.sha256(encoded).hexdigest()
        if self.sha256 is not None and self.sha256 != digest:
            raise ValueError("Inline evidence digest does not match content")
        self.sha256 = digest
        return self


class ReviewPin(Contract):
    """An attributed input record, not a quality-gate approval or reviewer-role assertion."""

    annotation_digest: Digest
    decision: Literal["accepted", "rejected", "abstained"]
    rationale: str = Field(min_length=1, max_length=2000)


class TransformationPin(InlineEvidence):
    format: Literal["verdant.transformation.v1"] = "verdant.transformation.v1"

    @model_validator(mode="after")
    def reproducible_definition(self):
        if set(self.payload) != {"code", "configuration", "environment"}:
            raise ValueError("Transformation pins require code, configuration and environment")
        if not isinstance(self.payload["code"], str) or not self.payload["code"]:
            raise ValueError("Transformation code must be nonempty text")
        if not all(
            isinstance(self.payload[name], dict) for name in ("configuration", "environment")
        ):
            raise ValueError("Transformation configuration/environment must be objects")
        return self


class InterpretationPins(Contract):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, serialize_by_alias=True)

    schema_name: Literal["verdant.membership.v1"] = Field(
        default="verdant.membership.v1", alias="schema"
    )
    annotation_revision: InlineEvidence | None = None
    task_schema_revision: InlineEvidence | None = None
    review_evidence: ReviewPin | None = None
    transformation: TransformationPin | None = None

    @model_validator(mode="after")
    def coherent_pins(self):
        if self.annotation_revision is not None and self.task_schema_revision is None:
            raise ValueError("Annotation evidence requires its exact task schema")
        if self.review_evidence is not None and (
            self.annotation_revision is None
            or self.review_evidence.annotation_digest != self.annotation_revision.sha256
        ):
            raise ValueError("Review evidence must bind the pinned annotation digest")
        if (
            len(
                json.dumps(
                    self.model_dump(by_alias=True), ensure_ascii=False, allow_nan=False
                ).encode()
            )
            > 65536
        ):
            raise ValueError("Membership interpretation exceeds 64 KiB")
        return self


class MembershipWrite(Contract):
    interpretation: InterpretationPins | None = None
    expected_revision: Revision
    sample_revision_id: UUID
    split: str = Field(min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_-]*$")


class Membership(Contract):
    interpretation: InterpretationPins = Field(default_factory=InterpretationPins)
    sample_id: UUID
    sample_revision_id: UUID
    split: str


class DraftChange(Contract):
    draft_id: UUID
    revision: int


class DraftPage(Contract):
    draft_id: UUID
    revision: int
    members: list[Membership]
    next_after: UUID | None = None
