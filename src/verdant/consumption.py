"""Exact retained-version reads and catalog availability through current authorization.

Logical snapshots never contain storage endpoints. Byte reads verify a bounded set of
qualified placements outside PostgreSQL transactions, then recheck current permission
before returning a preview, native stream or short-lived download redirect.
"""

import codecs
import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Literal
from uuid import UUID

from pydantic import ConfigDict, Field
from sqlalchemy import func, select

from verdant import contracts as c
from verdant.application import RegistryService
from verdant.domain import Conflict, NotFound
from verdant.identity import current_principal, require_current_access
from verdant.manifest import MANIFEST_SCHEMA
from verdant.persistence import (
    ArtifactLocationRow,
    ArtifactRow,
    DatasetRow,
    DatasetVersionRow,
    DraftRow,
    GenerationMemberRow,
    QualityPolicyRow,
    QualityReportRow,
    RegistryRepository,
    SampleArtifactRow,
    SampleRevisionRow,
    SampleRow,
)
from verdant.quality_service import Policy, QualityService, Report
from verdant.storage import (
    CHUNK_BYTES,
    ObjectCorrupt,
    ObjectDigest,
    ObjectMissing,
    S3Storage,
    StorageError,
    StorageUnavailable,
    configured_storage,
)
from verdant.telemetry import observed

PREVIEW_BYTES = 4096
PLACEMENT_ATTEMPTS = 4


class Version(c.Contract):
    id: UUID
    namespace_id: UUID
    dataset_id: UUID
    label: str
    generation_id: UUID
    candidate_id: UUID
    report_id: UUID
    manifest_artifact_id: UUID
    manifest_digest: str
    manifest_size: int
    created_at: datetime
    availability: Literal["active", "deprecated", "archived"]
    prior_availability: Literal["active", "deprecated"]
    availability_revision: int


class VersionPage(c.Contract):
    items: list[Version]
    total: int
    next_after: UUID | None = None


class LogicalArtifact(c.Contract):
    id: UUID
    sha256: str
    size_bytes: int


class LineageEntry(c.Contract):
    kind: Literal[
        "sample_revision",
        "annotation_revision",
        "task_schema_revision",
        "review_evidence",
        "transformation",
        "quality_report",
        "quality_policy",
    ]
    id: str | None
    status: Literal["present", "absent", "partial"]
    label: str


class FrozenMember(c.Contract):
    sample_id: UUID
    sample_revision_id: UUID
    revision: int
    split: str
    metadata: dict[str, c.JsonValue]
    interpretation: c.InterpretationPins
    artifact: LogicalArtifact
    lineage: list[LineageEntry]


class VersionMembers(c.Contract):
    version: Version
    items: list[FrozenMember]
    total: int
    next_after: UUID | None = None
    quality_policy: Policy
    quality_report: Report
    lineage_status: Literal["complete", "partial"]


class ManifestDescriptor(c.Contract):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, serialize_by_alias=True)
    artifact_id: UUID
    sha256: str
    size_bytes: int
    schema_name: Literal["verdant.manifest.v1"] = Field(default=MANIFEST_SCHEMA, alias="schema")
    download_path: str


class ArtifactDescriptor(c.Contract):
    artifact: LogicalArtifact
    download_path: str


class Preview(c.Contract):
    sample_id: UUID
    sample_revision_id: UUID
    artifact_id: UUID
    sha256: str
    size_bytes: int
    modality: str
    kind: Literal["text", "binary"]
    text: str | None
    truncated: bool
    warning: str | None
    download_path: str


class DatasetAvailability(c.Contract):
    expected_revision: c.Revision
    action: Literal["archive", "unarchive"]


class VersionAvailability(c.Contract):
    expected_revision: c.Revision
    action: Literal["deprecate", "activate", "archive", "unarchive"]


@dataclass(frozen=True)
class _Placement:
    id: UUID
    object_key: str = field(repr=False)


@dataclass(frozen=True)
class _Target:
    artifact: LogicalArtifact
    placements: tuple[_Placement, ...]
    sample_id: UUID | None = None
    revision_id: UUID | None = None
    modality: str | None = None


class _Prefix:
    def __init__(self):
        self.data = bytearray()

    def write(self, chunk):
        remaining = PREVIEW_BYTES - len(self.data)
        if remaining > 0:
            self.data.extend(chunk[:remaining])
        return len(chunk)


class ConsumptionService:
    def __init__(self, sessions, storage: S3Storage | None = None, *, store_id="primary"):
        self.sessions, self._storage, self.store_id = sessions, storage, store_id

    @property
    def storage(self):
        if self._storage is None:
            self._storage = configured_storage()
        return self._storage

    @contextmanager
    def _read(self, actor, namespace_id):
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id)
            require_current_access(session, actor.id, namespace_id)
            yield session

    @staticmethod
    def _version(session, namespace_id, dataset_id, version_id, *, lock=False):
        query = select(DatasetVersionRow).where(
            DatasetVersionRow.id == version_id,
            DatasetVersionRow.namespace_id == namespace_id,
            DatasetVersionRow.dataset_id == dataset_id,
        )
        if lock:
            query = query.with_for_update()
        row = session.scalar(query)
        if row is None:
            raise NotFound("Dataset version not found")
        return row

    @staticmethod
    def _view(row):
        return Version.model_validate({name: getattr(row, name) for name in Version.model_fields})

    @staticmethod
    def _base(namespace_id, dataset_id, version_id):
        return f"/api/v1/namespaces/{namespace_id}/datasets/{dataset_id}/versions/{version_id}"

    @observed("consumption")
    def list_versions(
        self, actor, namespace_id, dataset_id, after=None, limit=25, include_archived=False
    ):
        if not 1 <= limit <= 100:
            raise ValueError("Version page must be between 1 and 100")
        with self._read(actor, namespace_id) as session:
            if RegistryRepository(session).dataset(namespace_id, dataset_id) is None:
                raise NotFound("Dataset not found")
            filters = [
                DatasetVersionRow.namespace_id == namespace_id,
                DatasetVersionRow.dataset_id == dataset_id,
            ]
            if not include_archived:
                filters.append(DatasetVersionRow.availability != "archived")
            total = session.scalar(
                select(func.count()).select_from(DatasetVersionRow).where(*filters)
            )
            query = select(DatasetVersionRow).where(*filters).order_by(DatasetVersionRow.id)
            if after is not None:
                query = query.where(DatasetVersionRow.id > after)
            rows = session.scalars(query.limit(limit + 1)).all()
            return VersionPage(
                items=[self._view(row) for row in rows[:limit]],
                total=total,
                next_after=rows[limit - 1].id if len(rows) > limit else None,
            )

    @observed("consumption")
    def get_version(self, actor, namespace_id, dataset_id, version_id):
        with self._read(actor, namespace_id) as session:
            return self._view(self._version(session, namespace_id, dataset_id, version_id))

    @staticmethod
    def _quality(session, row):
        report = session.get(QualityReportRow, row.report_id)
        policy = session.get(QualityPolicyRow, report.policy_id) if report is not None else None
        if (
            report is None
            or policy is None
            or report.state != "complete"
            or report.candidate_id != row.candidate_id
            or report.generation_id != row.generation_id
            or report.namespace_id != row.namespace_id
            or policy.namespace_id != row.namespace_id
            or policy.dataset_id != row.dataset_id
            or policy.revision != report.policy_revision
        ):
            raise Conflict("Retained version quality evidence is incomplete")
        return QualityService._policy(policy), QualityService._report(report)

    @staticmethod
    def _lineage(member, pins, policy, report):
        entries = [
            LineageEntry(
                kind="sample_revision",
                id=str(member.sample_revision_id),
                status="present",
                label="Frozen sample revision",
            )
        ]
        for kind, title in (
            ("annotation_revision", "Annotation revision"),
            ("task_schema_revision", "Task schema revision"),
            ("review_evidence", "Review evidence"),
            ("transformation", "Transformation definition"),
        ):
            value = getattr(pins, kind)
            identity = None
            if value is not None:
                identity = (
                    getattr(value, "sha256", None)
                    or hashlib.sha256(
                        json.dumps(
                            value.model_dump(mode="json"),
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                            allow_nan=False,
                        ).encode()
                    ).hexdigest()
                )
            entries.append(
                LineageEntry(
                    kind=kind,
                    id=identity,
                    status="present" if identity else "absent",
                    label=title if identity else f"{title} not supplied in published snapshot",
                )
            )
        entries.extend(
            [
                LineageEntry(
                    kind="quality_report",
                    id=str(report.id),
                    status="present",
                    label="Pinned publication validation report",
                ),
                LineageEntry(
                    kind="quality_policy",
                    id=str(policy.id),
                    status="present",
                    label=f"Pinned policy {policy.name}, revision {policy.revision}",
                ),
            ]
        )
        return entries

    @staticmethod
    def _member_query(row):
        return (
            select(
                GenerationMemberRow, SampleRevisionRow, SampleArtifactRow, ArtifactRow, SampleRow
            )
            .join(
                SampleRevisionRow,
                (SampleRevisionRow.namespace_id == GenerationMemberRow.namespace_id)
                & (SampleRevisionRow.id == GenerationMemberRow.sample_revision_id),
            )
            .join(
                SampleRow,
                (SampleRow.namespace_id == SampleRevisionRow.namespace_id)
                & (SampleRow.id == SampleRevisionRow.sample_id),
            )
            .outerjoin(
                SampleArtifactRow,
                (SampleArtifactRow.namespace_id == GenerationMemberRow.namespace_id)
                & (SampleArtifactRow.revision_id == GenerationMemberRow.sample_revision_id),
            )
            .outerjoin(
                ArtifactRow,
                (ArtifactRow.namespace_id == SampleArtifactRow.namespace_id)
                & (ArtifactRow.id == SampleArtifactRow.artifact_id),
            )
            .where(
                GenerationMemberRow.namespace_id == row.namespace_id,
                GenerationMemberRow.generation_id == row.generation_id,
            )
        )

    @staticmethod
    def _logical(artifact):
        if (
            artifact is None
            or artifact.collection_state != "active"
            or artifact.verified_at is None
        ):
            raise Conflict("Retained membership has no active verified artifact identity")
        return LogicalArtifact(
            id=artifact.id, sha256=artifact.sha256, size_bytes=artifact.size_bytes
        )

    @observed("consumption")
    def members(self, actor, namespace_id, dataset_id, version_id, after=None, limit=25):
        if not 1 <= limit <= 100:
            raise ValueError("Member page must be between 1 and 100")
        with self._read(actor, namespace_id) as session:
            row = self._version(session, namespace_id, dataset_id, version_id)
            policy, report = self._quality(session, row)
            query = self._member_query(row).order_by(GenerationMemberRow.sample_id)
            if after is not None:
                query = query.where(GenerationMemberRow.sample_id > after)
            records = session.execute(query.limit(limit + 1)).all()
            items = []
            for member, revision, _, artifact, _ in records[:limit]:
                pins = c.InterpretationPins.model_validate(member.interpretation)
                items.append(
                    FrozenMember(
                        sample_id=member.sample_id,
                        sample_revision_id=revision.id,
                        revision=revision.revision,
                        split=member.split,
                        metadata=revision.metadata_json,
                        interpretation=pins,
                        artifact=self._logical(artifact),
                        lineage=self._lineage(member, pins, policy, report),
                    )
                )
            return VersionMembers(
                version=self._view(row),
                items=items,
                total=report.expected_count,
                next_after=records[limit - 1][0].sample_id if len(records) > limit else None,
                quality_policy=policy,
                quality_report=report,
                lineage_status="partial"
                if any(entry.status != "present" for item in items for entry in item.lineage)
                else "complete",
            )

    @observed("consumption")
    def manifest(self, actor, namespace_id, dataset_id, version_id):
        with self._read(actor, namespace_id) as session:
            row = self._version(session, namespace_id, dataset_id, version_id)
            return ManifestDescriptor(
                artifact_id=row.manifest_artifact_id,
                sha256=row.manifest_digest,
                size_bytes=row.manifest_size,
                download_path=self._base(namespace_id, dataset_id, version_id)
                + "/manifest/download",
            )

    def _target(self, actor, namespace_id, dataset_id, version_id, sample_id=None):
        # Authorize and bind the logical artifact before resolving any storage location.
        with self._read(actor, namespace_id) as session:
            row = self._version(session, namespace_id, dataset_id, version_id)
            revision_id = modality = None
            if sample_id is None:
                artifact = session.get(ArtifactRow, row.manifest_artifact_id)
                logical = self._logical(artifact)
                if (logical.sha256, logical.size_bytes) != (row.manifest_digest, row.manifest_size):
                    raise Conflict(
                        "Retained manifest identity does not match its published version"
                    )
            else:
                record = session.execute(
                    self._member_query(row).where(GenerationMemberRow.sample_id == sample_id)
                ).one_or_none()
                if record is None:
                    raise NotFound("Sample is not a member of this retained version")
                _, revision, _, artifact, sample = record
                logical = self._logical(artifact)
                revision_id, modality = revision.id, sample.modality
            storage = self.storage
            locations = session.scalars(
                select(ArtifactLocationRow)
                .where(
                    ArtifactLocationRow.namespace_id == namespace_id,
                    ArtifactLocationRow.artifact_id == logical.id,
                    ArtifactLocationRow.store_id == self.store_id,
                    ArtifactLocationRow.bucket == storage.settings.bucket,
                )
                .order_by(ArtifactLocationRow.id)
                .limit(PLACEMENT_ATTEMPTS)
            ).all()
            if not locations:
                raise StorageUnavailable("Retained artifact has no qualified storage placement")
            return _Target(
                logical,
                tuple(_Placement(location.id, location.object_key) for location in locations),
                sample_id,
                revision_id,
                modality,
            )

    def _verify(self, actor, namespace_id, target, *, destination=None, prefix=False):
        errors = []
        result = None
        for placement in target.placements:
            collector = _Prefix() if prefix else destination
            if destination is not None:
                destination.seek(0)
                destination.truncate()
            try:
                self.storage.verify(
                    placement.object_key,
                    ObjectDigest(target.artifact.sha256, target.artifact.size_bytes),
                    destination=collector,
                )
                result = (placement, bytes(collector.data) if prefix else None)
                break
            except StorageError as exc:
                errors.append(exc)
        # Failed reads also must not disclose artifact state to a now-denied principal.
        with self._read(actor, namespace_id):
            if result is None:
                if any(isinstance(error, ObjectCorrupt) for error in errors):
                    raise ObjectCorrupt(
                        "Retained bytes fail digest verification in checked placements"
                    )
                if any(not isinstance(error, ObjectMissing) for error in errors):
                    raise StorageUnavailable("Retained artifact storage is unavailable")
                raise ObjectMissing("Retained bytes are missing from checked placements")
        return result

    @observed("consumption")
    def sample_descriptor(self, actor, namespace_id, dataset_id, version_id, sample_id):
        target = self._target(actor, namespace_id, dataset_id, version_id, sample_id)
        return ArtifactDescriptor(
            artifact=target.artifact,
            download_path=self._base(namespace_id, dataset_id, version_id)
            + f"/samples/{sample_id}/download",
        )

    @observed("consumption")
    def preview(self, actor, namespace_id, dataset_id, version_id, sample_id):
        target = self._target(actor, namespace_id, dataset_id, version_id, sample_id)
        _, prefix = self._verify(actor, namespace_id, target, prefix=True)
        kind, text, warning = (
            "binary",
            None,
            "Binary preview is unsupported; use verified download.",
        )
        truncated = target.artifact.size_bytes > len(prefix)
        modality = target.modality.lower().split(";", 1)[0]
        if modality in {
            "text",
            "json",
            "csv",
            "markdown",
            "application/json",
        } or modality.startswith("text/"):
            try:
                text = codecs.getincrementaldecoder("utf-8")().decode(prefix, final=not truncated)
                if "\x00" in text:
                    raise UnicodeDecodeError("utf-8", prefix, 0, 1, "binary control byte")
                kind, warning = "text", None
            except UnicodeDecodeError:
                text = None
                warning = "Content is not UTF-8 text; use verified binary download."
        with self._read(actor, namespace_id):
            return Preview(
                sample_id=sample_id,
                sample_revision_id=target.revision_id,
                artifact_id=target.artifact.id,
                sha256=target.artifact.sha256,
                size_bytes=target.artifact.size_bytes,
                modality=target.modality,
                kind=kind,
                text=text,
                truncated=truncated if kind == "text" else False,
                warning=warning,
                download_path=self._base(namespace_id, dataset_id, version_id)
                + f"/samples/{sample_id}/download",
            )

    def download(self, actor, namespace_id, dataset_id, version_id, sample_id=None):
        """REST redirect only: never persist/log the short-lived URL or expose it in JSON."""
        target = self._target(actor, namespace_id, dataset_id, version_id, sample_id)
        placement, _ = self._verify(actor, namespace_id, target)
        with self._read(actor, namespace_id):
            return self.storage.sign_read(placement.object_key)

    @observed("consumption")
    def stream_manifest(self, actor, namespace_id, dataset_id, version_id, destination: BinaryIO):
        """Stage a verified bounded artifact on worker scratch before releasing any bytes.

        Alternative placements reset the scratch file; failed copies never contaminate
        a successful stream. Destination errors propagate and callers discard partial output.
        """
        target = self._target(actor, namespace_id, dataset_id, version_id)
        with SpooledTemporaryFile(max_size=CHUNK_BYTES, mode="w+b") as scratch:
            self._verify(actor, namespace_id, target, destination=scratch)
            scratch.seek(0)
            with self._read(actor, namespace_id):
                while chunk := scratch.read(CHUNK_BYTES):
                    offset = 0
                    while offset < len(chunk):
                        count = destination.write(chunk[offset:])
                        if type(count) is not int or not 0 < count <= len(chunk) - offset:
                            raise OSError("Manifest destination did not accept requested bytes")
                        offset += count
        return ObjectDigest(target.artifact.sha256, target.artifact.size_bytes)

    @observed("availability")
    def dataset_availability(self, actor, namespace_id, dataset_id, command: DatasetAvailability):
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id, write=True)
            row = session.scalar(
                select(DatasetRow)
                .where(
                    DatasetRow.id == dataset_id,
                    DatasetRow.namespace_id == namespace_id,
                )
                .with_for_update()
            )
            if row is None:
                raise NotFound("Dataset not found")
            require_current_access(session, actor.id, namespace_id, write=True)
            if row.revision != command.expected_revision:
                raise Conflict(
                    "Dataset changed; reload its current metadata and availability revision"
                )
            desired = "archived" if command.action == "archive" else "active"
            if row.availability != desired:
                if row.revision == 9223372036854775807:
                    raise Conflict("Dataset revision limit reached")
                previous = row.availability
                row.availability, row.revision = desired, row.revision + 1
                RegistryService._audit(
                    RegistryRepository(session),
                    actor,
                    namespace_id,
                    row.id,
                    "dataset.availability_changed",
                    {"before": previous, "availability": desired, "revision": row.revision},
                )
            draft = session.scalar(
                select(DraftRow).where(
                    DraftRow.namespace_id == namespace_id,
                    DraftRow.dataset_id == dataset_id,
                )
            )
            return RegistryService._dataset(row, draft.id)

    @observed("availability")
    def version_availability(
        self, actor, namespace_id, dataset_id, version_id, command: VersionAvailability
    ):
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id, write=True)
            dataset = session.scalar(
                select(DatasetRow)
                .where(
                    DatasetRow.id == dataset_id,
                    DatasetRow.namespace_id == namespace_id,
                )
                .with_for_update()
            )
            if dataset is None:
                raise NotFound("Dataset not found")
            row = self._version(session, namespace_id, dataset_id, version_id, lock=True)
            require_current_access(session, actor.id, namespace_id, write=True)
            if row.availability_revision != command.expected_revision:
                raise Conflict("Version availability changed; reload its current revision")
            if command.action in {"deprecate", "activate"}:
                if row.availability == "archived":
                    raise Conflict("Unarchive the version before changing deprecation")
                desired = "deprecated" if command.action == "deprecate" else "active"
                prior = desired
            elif command.action == "archive":
                desired = "archived"
                prior = (
                    row.availability if row.availability != "archived" else row.prior_availability
                )
            else:
                desired, prior = row.prior_availability, row.prior_availability
            if row.availability != desired:
                if row.availability_revision == 9223372036854775807:
                    raise Conflict("Version availability revision limit reached")
                previous = row.availability
                row.availability, row.prior_availability = desired, prior
                row.availability_revision += 1
                session.flush([row])
                RegistryService._audit(
                    RegistryRepository(session),
                    actor,
                    namespace_id,
                    row.id,
                    "version.availability_changed",
                    {
                        "before": previous,
                        "availability": desired,
                        "revision": row.availability_revision,
                    },
                )
            return self._view(row)
