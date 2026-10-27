"""Versioned, bounded API and shared-service contracts."""

import json
import math
from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

CONTRACT_VERSION = 1
EVENT_VERSION = 1
MANIFEST_VERSION = 1  # Reserved encoding version; publication is not implemented yet.
SCHEMA_REVISION = "0010_workflow_inbox"

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


class MembershipWrite(Contract):
    expected_revision: Revision
    sample_revision_id: UUID
    split: str = Field(min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_-]*$")


class Membership(Contract):
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
