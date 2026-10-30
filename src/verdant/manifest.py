"""Version-one manifest bytes, independent of artifact storage placement.

The caller supplies authorized, pinned policy/report evidence and members in UUID order.
This writer does not approve quality gates, verify remote bytes, or publish a version.
On any error the destination is partial and must be discarded; only a returned digest
describes a complete manifest. The caller owns flushing, persistence and file lifetime.
"""

import hashlib
import json
from collections.abc import Iterable
from typing import BinaryIO
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from verdant.contracts import Contract, Digest, InterpretationPins, Revision
from verdant.storage import MAX_ARTIFACT_BYTES, ObjectDigest

MANIFEST_SCHEMA = "verdant.manifest.v1"
WRITE_CHUNK_BYTES = 16 * 1024


class ManifestTooLarge(ValueError):
    """The valid candidate exceeds the supported single-manifest capacity."""


_ENCODER = json.JSONEncoder(
    sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
)


class ManifestArtifact(Contract):
    id: UUID
    sha256: Digest
    size_bytes: int = Field(ge=0, le=MAX_ARTIFACT_BYTES, strict=True)


class ManifestHeader(Contract):
    namespace_id: UUID
    dataset_id: UUID
    candidate_id: UUID
    generation_id: UUID
    source_revision: Revision
    policy: dict[str, JsonValue]
    validation_report: dict[str, JsonValue]


class ManifestMember(Contract):
    sample_id: UUID
    sample_revision_id: UUID
    split: str = Field(min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_-]*$")
    metadata: dict[str, JsonValue]
    interpretation: InterpretationPins
    artifact: ManifestArtifact

    @model_validator(mode="after")
    def bounded_metadata(self):
        size = 0
        for token in _ENCODER.iterencode(self.metadata):
            size += len(token.encode("utf-8"))
            if size > 65536:
                raise ValueError("Manifest member metadata exceeds 64 KiB")
        return self


def write_manifest(
    destination: BinaryIO,
    header: ManifestHeader,
    members: Iterable[ManifestMember],
    *,
    expected_member_count: int,
) -> ObjectDigest:
    """Write compact, sorted-key UTF-8 JSON while retaining only the current member.

    This is Verdant's fixed Python JSON encoding, not an RFC 8785 implementation.
    Members must have strictly increasing sample UUIDs, matching PostgreSQL UUID order.
    The output cap is checked before every bounded destination write. Short positive
    writes are completed; failed/zero/invalid writes never produce a success receipt.
    """
    if (
        isinstance(expected_member_count, bool)
        or not isinstance(expected_member_count, int)
        or not 0 <= expected_member_count <= 9223372036854775807
    ):
        raise ValueError("Expected member count must be a nonnegative 64-bit integer")
    # Revalidate a snapshot so a previously constructed but subsequently mutated model
    # cannot bypass the fixed schema or nested JSON/interpretation validation.
    header = ManifestHeader.model_validate(header.model_dump(by_alias=True))
    fields = header.model_dump(mode="json", by_alias=True) | {"schema": MANIFEST_SCHEMA}
    hasher = hashlib.sha256()
    size = 0

    def write_bytes(data: bytes):
        nonlocal size
        for start in range(0, len(data), WRITE_CHUNK_BYTES):
            chunk = data[start : start + WRITE_CHUNK_BYTES]
            if len(chunk) > MAX_ARTIFACT_BYTES - size:
                raise ManifestTooLarge("Manifest exceeds supported artifact size")
            offset = 0
            while offset < len(chunk):
                accepted = destination.write(chunk[offset:])
                if (
                    isinstance(accepted, bool)
                    or not isinstance(accepted, int)
                    or not 0 < accepted <= len(chunk) - offset
                ):
                    raise OSError("Manifest destination did not accept the requested bytes")
                hasher.update(chunk[offset : offset + accepted])
                size += accepted
                offset += accepted

    def write_json(value):
        for token in _ENCODER.iterencode(value):
            write_bytes(token.encode("utf-8"))

    write_bytes(b"{")
    count = 0
    previous: UUID | None = None
    for position, key in enumerate(sorted([*fields, "members"])):
        if position:
            write_bytes(b",")
        write_json(key)
        write_bytes(b":")
        if key != "members":
            write_json(fields[key])
            continue
        write_bytes(b"[")
        for member in members:
            member = ManifestMember.model_validate(member.model_dump(by_alias=True))
            if previous is not None and member.sample_id.int <= previous.int:
                raise ValueError("Manifest sample UUIDs must be unique and strictly increasing")
            if count >= expected_member_count:
                raise ValueError("Manifest has more members than expected")
            if count:
                write_bytes(b",")
            write_json(member.model_dump(mode="json", by_alias=True))
            previous = member.sample_id
            count += 1
        if count != expected_member_count:
            raise ValueError("Manifest member count is incomplete")
        write_bytes(b"]")
    write_bytes(b"}")
    return ObjectDigest(sha256=hasher.hexdigest(), size_bytes=size)
