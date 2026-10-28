"""Exact verified-revision reads and bounded inert previews; never render source markup."""

import base64
import codecs
import warnings
from io import BytesIO
from tempfile import SpooledTemporaryFile
from typing import Literal
from uuid import UUID

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import Field
from sqlalchemy import select

from verdant.application import RegistryService
from verdant.contracts import Contract
from verdant.domain import Conflict, NotFound
from verdant.persistence import (
    ArtifactLocationRow,
    ArtifactRow,
    SampleArtifactRow,
    SampleRevisionRow,
    SampleRow,
)
from verdant.storage import CHUNK_BYTES, URL_SECONDS, ObjectDigest, StorageUnavailable
from verdant.telemetry import observed

TEXT_BYTES = 64 * 1024
IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_PIXELS = 20_000_000
THUMBNAIL_EDGE = 1024


class SamplePreview(Contract):
    revision_id: UUID
    artifact_id: UUID
    sha256: str
    size_bytes: int
    kind: Literal["text", "image", "download"]
    text: str | None = None
    image_png_base64: str | None = None
    truncated: bool = False
    reason: str | None = None
    download_url: str = Field(repr=False)
    expires_in: int = URL_SECONDS


class PreviewService:
    def __init__(self, sessions, storage, *, store_id="primary"):
        self.registry = RegistryService(sessions)
        self.storage = storage
        self.store_id = store_id

    def _binding(self, actor, namespace_id, sample_id, revision_id):
        with self.registry._transaction(actor, namespace_id) as repo:
            row = repo.session.execute(
                select(SampleRevisionRow, SampleRow.modality, ArtifactRow, ArtifactLocationRow)
                .join(
                    SampleRow,
                    (SampleRow.namespace_id == SampleRevisionRow.namespace_id)
                    & (SampleRow.id == SampleRevisionRow.sample_id),
                )
                .join(SampleArtifactRow, SampleArtifactRow.revision_id == SampleRevisionRow.id)
                .join(ArtifactRow, ArtifactRow.id == SampleArtifactRow.artifact_id)
                .join(ArtifactLocationRow, ArtifactLocationRow.artifact_id == ArtifactRow.id)
                .where(
                    SampleRevisionRow.namespace_id == namespace_id,
                    SampleRevisionRow.sample_id == sample_id,
                    SampleRevisionRow.id == revision_id,
                    SampleArtifactRow.namespace_id == namespace_id,
                    SampleArtifactRow.sample_id == sample_id,
                    ArtifactRow.namespace_id == namespace_id,
                    ArtifactRow.verified_at.is_not(None),
                    ArtifactLocationRow.namespace_id == namespace_id,
                    ArtifactLocationRow.store_id == self.store_id,
                    ArtifactLocationRow.object_key == SampleRevisionRow.object_key,
                )
            ).first()
            if row is None:
                raise NotFound("Verified sample content not found")
            revision, modality, artifact, location = row
            if self.storage is None:
                raise StorageUnavailable("Object storage is not configured")
            if location.bucket != self.storage.settings.bucket or (
                revision.sha256,
                revision.size_bytes,
            ) != (artifact.sha256, artifact.size_bytes):
                raise Conflict("Verified sample content does not match configured storage")
            return artifact.id, location.object_key, artifact.sha256, artifact.size_bytes, modality

    @observed("preview")
    def get(self, actor, namespace_id, sample_id, revision_id):
        binding = self._binding(actor, namespace_id, sample_id, revision_id)
        artifact_id, key, sha256, size, modality = binding
        fields = {"kind": "download", "reason": "No inline renderer for this content"}
        # Verify every byte before exposing either a preview or an attachment capability.
        # The spool spills to disk above one MiB and never holds a dataset in API memory.
        with SpooledTemporaryFile(max_size=CHUNK_BYTES, mode="w+b") as spool:
            self.storage.verify(key, ObjectDigest(sha256, size), destination=spool)
            spool.seek(0)
            if size <= IMAGE_BYTES:
                fields = self._image(spool) or fields
            if fields["kind"] == "download" and modality in {"text", "document", "transcription"}:
                spool.seek(0)
                data = spool.read(TEXT_BYTES)
                if not data.startswith(b"%PDF-"):
                    try:
                        # An incremental decoder retains an incomplete final code point.
                        text = codecs.getincrementaldecoder("utf-8")().decode(
                            data, final=size <= TEXT_BYTES
                        )
                        if "\x00" not in text:
                            fields = {"kind": "text", "text": text, "truncated": size > TEXT_BYTES}
                    except UnicodeDecodeError:
                        pass
        url = self.storage.sign_read(key)
        # Current grants and exact placement are rechecked after all provider I/O/signing.
        if self._binding(actor, namespace_id, sample_id, revision_id) != binding:
            raise Conflict("Verified sample content changed")
        return SamplePreview(
            revision_id=revision_id,
            artifact_id=artifact_id,
            sha256=sha256,
            size_bytes=size,
            download_url=url,
            **fields,
        )

    @staticmethod
    def _image(spool):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(spool, formats=["PNG", "JPEG"]) as source:
                    if source.width * source.height > IMAGE_PIXELS:
                        return {
                            "kind": "download",
                            "reason": "Image exceeds 20 million pixel preview limit",
                        }
                    source.thumbnail((THUMBNAIL_EDGE, THUMBNAIL_EDGE))
                    image = ImageOps.exif_transpose(source).convert("RGBA")
                    # Reconstruct pixels only: no source EXIF, scripts or ancillary metadata.
                    clean = Image.frombytes("RGBA", image.size, image.tobytes())
                    output = BytesIO()
                    clean.save(output, format="PNG")
                    return {
                        "kind": "image",
                        "image_png_base64": base64.b64encode(output.getvalue()).decode("ascii"),
                    }
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ):
            return None
