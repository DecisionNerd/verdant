"""Protected publication preparation and constant-size atomic version finalization.

Public commands request work. Only the trusted runner accepts independently verified
storage observations; no transport accepts readiness, digests or verification claims.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from verdant.application import RegistryService
from verdant.contracts import Contract, InterpretationPins, Revision
from verdant.domain import Conflict, NotFound, Principal
from verdant.identity import current_principal, require_current_access
from verdant.manifest import ManifestArtifact, ManifestHeader, ManifestMember
from verdant.operations import OperationRequest, OperationService
from verdant.persistence import (
    ArtifactLocationRow,
    ArtifactRow,
    CandidateRow,
    DatasetRow,
    DatasetVersionRow,
    DraftRow,
    GenerationMemberRow,
    OperationRow,
    PublicationGroupRow,
    QualityPolicyRow,
    QualityReportRow,
    RegistryRepository,
    RetentionReferenceRow,
    SampleArtifactRow,
    SampleRevisionRow,
)
from verdant.quality import QualityDefinition
from verdant.quality_service import QualityService, RuleEvidence
from verdant.storage import ObjectDigest
from verdant.telemetry import observed


class PublicationStart(Contract):
    request_key: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    report_id: UUID
    mode: Literal["selected_candidate", "current_draft"] = "selected_candidate"


class PublicationBatch(Contract):
    expected_count: Revision
    limit: int = Field(default=10, ge=1, le=25, strict=True)


class PublicationRelease(Contract):
    limit: int = Field(default=100, ge=1, le=100, strict=True)


class EmptyCommand(Contract):
    pass


class Publication(Contract):
    id: UUID
    operation_id: UUID
    candidate_id: UUID
    generation_id: UUID
    report_id: UUID
    policy_id: UUID
    source_revision: int
    latest_draft_revision: int
    label: str
    mode: Literal["selected_candidate", "current_draft"]
    state: Literal["building", "prepared", "published", "releasing", "released"]
    expected_count: int
    prepared_count: int
    expires_at: datetime
    expired: bool
    last_error: str | None
    manifest_digest: str | None
    manifest_size: int | None
    version_id: UUID | None


class PublicationPage(Contract):
    items: list[Publication]
    next_after: UUID | None = None


@dataclass(frozen=True)
class ArtifactSource:
    id: UUID
    location_id: UUID
    sha256: str
    size_bytes: int
    object_key: str

    @property
    def digest(self):
        return ObjectDigest(self.sha256, self.size_bytes)


@dataclass(frozen=True)
class MemberSource:
    sample_id: UUID
    revision_id: UUID
    artifact: ArtifactSource


@dataclass(frozen=True)
class PreparationBatch:
    group_id: UUID
    epoch: int
    starting_count: int
    previous_cursor: UUID | None
    cursor: UUID | None
    members: tuple[MemberSource, ...]


@dataclass(frozen=True)
class ManifestWork:
    group_id: UUID
    epoch: int
    expected_count: int
    header: ManifestHeader


class PublicationService:
    def __init__(self, sessions, *, bucket: str, store_id: str = "primary"):
        if not bucket or len(bucket) > 255 or not store_id or len(store_id) > 100:
            raise ValueError("Invalid publication artifact store")
        self.sessions = sessions
        self.bucket, self.store_id = bucket, store_id
        self.operations = OperationService(sessions)

    @staticmethod
    def _now(session):
        return session.scalar(select(func.clock_timestamp()))

    @staticmethod
    def _group(session, namespace_id, dataset_id, group_id, *, lock=False):
        query = select(PublicationGroupRow).where(
            PublicationGroupRow.id == group_id,
            PublicationGroupRow.namespace_id == namespace_id,
            PublicationGroupRow.dataset_id == dataset_id,
        )
        if lock:
            query = query.with_for_update()
        row = session.scalar(query)
        if row is None:
            raise NotFound("Publication preparation not found")
        return row

    def _write_guards(self, session, actor, namespace_id, dataset_id, group_id):
        current_principal(session, actor.id).require(namespace_id, write=True)
        group = self._group(session, namespace_id, dataset_id, group_id, lock=True)
        operation = session.scalar(
            select(OperationRow)
            .where(OperationRow.id == group.id, OperationRow.namespace_id == namespace_id)
            .with_for_update()
        )
        current_principal(session, actor.id).require(namespace_id, write=True)
        if operation is None or operation.kind != "publish_dataset_version":
            raise Conflict("Publication operation identity is unavailable")
        return group, operation

    @staticmethod
    def _record(session, actor, operation, **payload):
        if operation.revision >= 9223372036854775807:
            raise Conflict("Operation revision limit reached")
        operation.revision += 1
        OperationService._record_transition(session, actor, operation, extra=payload)

    @staticmethod
    def _audit(session, actor, group, action, **payload):
        RegistryService._audit(
            RegistryRepository(session),
            actor,
            group.namespace_id,
            group.id,
            action,
            {
                "dataset_id": str(group.dataset_id),
                "candidate_id": str(group.candidate_id),
                "epoch": group.epoch,
            }
            | payload,
        )

    def _view(self, session, group):
        candidate = session.get(CandidateRow, group.candidate_id)
        draft = session.get(DraftRow, candidate.draft_id)
        report = session.get(QualityReportRow, group.report_id)
        operation = session.get(OperationRow, group.id)
        version_id = session.scalar(
            select(DatasetVersionRow.id).where(DatasetVersionRow.group_id == group.id)
        )
        return Publication(
            id=group.id,
            operation_id=group.id,
            candidate_id=group.candidate_id,
            generation_id=group.generation_id,
            report_id=group.report_id,
            policy_id=report.policy_id,
            source_revision=candidate.source_revision,
            latest_draft_revision=draft.revision,
            label=operation.inputs["label"],
            mode=operation.inputs["mode"],
            state=group.state,
            expected_count=group.expected_count,
            prepared_count=group.prepared_count,
            expires_at=group.expires_at,
            expired=group.state != "published" and group.expires_at <= self._now(session),
            last_error=group.last_error,
            manifest_digest=group.manifest_digest,
            manifest_size=group.manifest_size,
            version_id=version_id,
        )

    @staticmethod
    def _gate(session, namespace_id, dataset_id, candidate, report_id):
        report = session.scalar(
            select(QualityReportRow).where(
                QualityReportRow.id == report_id,
                QualityReportRow.namespace_id == namespace_id,
                QualityReportRow.dataset_id == dataset_id,
                QualityReportRow.candidate_id == candidate.id,
                QualityReportRow.generation_id == candidate.generation_id,
            )
        )
        if report is None:
            raise NotFound("Exact candidate validation report not found")
        policy = session.get(QualityPolicyRow, report.policy_id)
        if (
            report.state != "complete"
            or report.expected_count != candidate.prepared_count
            or report.processed_count != candidate.prepared_count
            or policy is None
            or policy.namespace_id != namespace_id
            or policy.dataset_id != dataset_id
            or report.policy_revision != policy.revision
        ):
            raise Conflict("Publication requires complete validation of this exact generation")
        definition = QualityDefinition.model_validate(policy.definition)
        results = [RuleEvidence.model_validate(item) for item in report.results]
        if len(results) != len(definition.rules):
            raise Conflict("Validation evidence does not match its policy")
        for rule, result in zip(definition.rules, results, strict=True):
            if (
                rule.id != result.rule_id
                or rule.required != result.required
                or rule.split != result.split
                or (rule.required and result.status != "passed")
            ):
                raise Conflict("Failed, unknown or mismatched required gates block publication")
        return report, policy

    @observed("publication")
    def start(
        self,
        actor: Principal,
        namespace_id: UUID,
        dataset_id: UUID,
        candidate_id: UUID,
        command: PublicationStart,
    ) -> Publication:
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id, write=True)
            dataset = session.scalar(
                select(DatasetRow)
                .where(DatasetRow.id == dataset_id, DatasetRow.namespace_id == namespace_id)
                .with_for_update()
            )
            if dataset is None:
                raise NotFound("Dataset not found")
            request = OperationRequest(
                request_key=command.request_key,
                kind="publish_dataset_version",
                inputs=command.model_dump(mode="json", exclude={"request_key"})
                | {"dataset_id": str(dataset_id), "candidate_id": str(candidate_id)},
            )
            # Admission/replay serializes on the dataset and uses Operations' exact request digest.
            operation = self.operations._admit(
                session, actor, namespace_id, request, initial_status="running"
            )
            prior = session.get(PublicationGroupRow, operation.id)
            require_current_access(session, actor.id, namespace_id, write=True)
            if prior is not None:
                return self._view(session, prior)
            if dataset.availability != "active":
                raise Conflict("Archived datasets pause new publication preparation")
            candidate = session.scalar(
                select(CandidateRow)
                .join(DraftRow, DraftRow.id == CandidateRow.draft_id)
                .where(
                    CandidateRow.id == candidate_id,
                    CandidateRow.namespace_id == namespace_id,
                    DraftRow.dataset_id == dataset_id,
                )
            )
            if candidate is None:
                raise NotFound("Candidate not found")
            if candidate.state != "sealed":
                raise Conflict("Seal candidate membership before publication")
            self._gate(session, namespace_id, dataset_id, candidate, command.report_id)
            row = PublicationGroupRow(
                id=operation.id,
                namespace_id=namespace_id,
                dataset_id=dataset_id,
                candidate_id=candidate.id,
                generation_id=candidate.generation_id,
                report_id=command.report_id,
                expected_count=candidate.prepared_count,
            )
            session.add(row)
            session.flush()
            self._audit(session, actor, row, "publication.started", report_id=str(row.report_id))
            return self._view(session, row)

    @observed("publication")
    def get(self, actor, namespace_id, dataset_id, group_id):
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id)
            return self._view(session, self._group(session, namespace_id, dataset_id, group_id))

    @observed("publication")
    def list(self, actor, namespace_id, dataset_id, candidate_id, after=None, limit=25):
        if not 1 <= limit <= 100:
            raise ValueError("Publication page must be between 1 and 100")
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id)
            candidate = session.scalar(
                select(CandidateRow)
                .join(DraftRow, DraftRow.id == CandidateRow.draft_id)
                .where(
                    CandidateRow.id == candidate_id,
                    CandidateRow.namespace_id == namespace_id,
                    DraftRow.dataset_id == dataset_id,
                )
            )
            if candidate is None:
                raise NotFound("Candidate not found")
            query = select(PublicationGroupRow).where(
                PublicationGroupRow.namespace_id == namespace_id,
                PublicationGroupRow.dataset_id == dataset_id,
                PublicationGroupRow.candidate_id == candidate_id,
            )
            if after is not None:
                query = query.where(PublicationGroupRow.id > after)
            rows = session.scalars(query.order_by(PublicationGroupRow.id).limit(limit + 1)).all()
            return PublicationPage(
                items=[self._view(session, row) for row in rows[:limit]],
                next_after=rows[limit - 1].id if len(rows) > limit else None,
            )

    def _building(self, session, group, operation):
        if group.state != "building" or operation.status != "running":
            raise Conflict("Publication does not permit preparation; reload its committed state")
        if group.expires_at <= self._now(session):
            raise Conflict("Preparation expired. Renew protection and recheck its artifacts")

    @staticmethod
    def _members(session, group, after=None, limit=25):
        query = (
            select(GenerationMemberRow, SampleRevisionRow, SampleArtifactRow, ArtifactRow)
            .join(
                SampleRevisionRow,
                (SampleRevisionRow.namespace_id == GenerationMemberRow.namespace_id)
                & (SampleRevisionRow.id == GenerationMemberRow.sample_revision_id),
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
                GenerationMemberRow.namespace_id == group.namespace_id,
                GenerationMemberRow.generation_id == group.generation_id,
            )
        )
        if after is not None:
            query = query.where(GenerationMemberRow.sample_id > after)
        return session.execute(query.order_by(GenerationMemberRow.sample_id).limit(limit)).all()

    def _source(self, session, namespace_id, artifact):
        if artifact is None or artifact.collection_state != "active":
            raise Conflict("A required artifact is missing or being collected")
        location = session.scalar(
            select(ArtifactLocationRow)
            .where(
                ArtifactLocationRow.namespace_id == namespace_id,
                ArtifactLocationRow.artifact_id == artifact.id,
                ArtifactLocationRow.store_id == self.store_id,
                ArtifactLocationRow.bucket == self.bucket,
            )
            .order_by(ArtifactLocationRow.id)
            .limit(1)
        )
        if location is None:
            raise Conflict("Required artifact has no location in the configured authorized store")
        return ArtifactSource(
            artifact.id, location.id, artifact.sha256, artifact.size_bytes, location.object_key
        )

    @staticmethod
    def _protect(session, group, artifact_ids):
        for artifact_id in sorted(set(artifact_ids)):
            artifact = session.scalar(
                select(ArtifactRow)
                .where(
                    ArtifactRow.id == artifact_id, ArtifactRow.namespace_id == group.namespace_id
                )
                .with_for_update()
            )
            if artifact is None or artifact.collection_state != "active":
                raise Conflict("Artifact protection lost the race with collection")
            session.execute(
                insert(RetentionReferenceRow)
                .values(group_id=group.id, artifact_id=artifact_id, namespace_id=group.namespace_id)
                .on_conflict_do_nothing()
            )

    @observed("publication")
    def claim_batch(self, actor, namespace_id, dataset_id, group_id, command: PublicationBatch):
        with self.sessions.begin() as session:
            group, operation = self._write_guards(
                session, actor, namespace_id, dataset_id, group_id
            )
            if command.expected_count > group.prepared_count:
                raise Conflict("Preparation checkpoint is ahead of committed progress")
            if command.expected_count < group.prepared_count or group.state in {
                "prepared",
                "published",
            }:
                return None
            self._building(session, group, operation)
            rows = self._members(session, group, group.cursor, command.limit)
            count = group.prepared_count + len(rows)
            if count > group.expected_count or (
                len(rows) < command.limit and count != group.expected_count
            ):
                raise Conflict("Sealed generation count does not match preparation")
            if any(binding is None or artifact is None for _, _, binding, artifact in rows):
                raise Conflict("Every frozen sample revision requires a verified artifact binding")
            self._protect(session, group, [artifact.id for _, _, _, artifact in rows])
            require_current_access(session, actor.id, namespace_id, write=True)
            members = tuple(
                MemberSource(
                    member.sample_id, revision.id, self._source(session, namespace_id, artifact)
                )
                for member, revision, _, artifact in rows
            )
            group.epoch += 1
            group.last_error = None
            session.flush()
            self._audit(
                session,
                actor,
                group,
                "publication.preparation_claimed",
                prepared_count=group.prepared_count,
            )
            return PreparationBatch(
                group.id,
                group.epoch,
                group.prepared_count,
                group.cursor,
                rows[-1][0].sample_id if rows else group.cursor,
                members,
            )

    def accept_batch(self, actor, namespace_id, dataset_id, proof: PreparationBatch):
        """Trusted runner only: proof follows live storage verification, never an HTTP body."""
        with self.sessions.begin() as session:
            group, operation = self._write_guards(
                session, actor, namespace_id, dataset_id, proof.group_id
            )
            self._building(session, group, operation)
            if (
                group.epoch != proof.epoch
                or group.prepared_count != proof.starting_count
                or group.cursor != proof.previous_cursor
            ):
                raise Conflict("Preparation attempt is stale; committed progress is retained")
            rows = (
                self._members(session, group, proof.previous_cursor, len(proof.members))
                if proof.members
                else []
            )
            identities = [(m.sample_id, r.id, a.id, a.sha256, a.size_bytes) for m, r, _, a in rows]
            expected = [
                (
                    m.sample_id,
                    m.revision_id,
                    m.artifact.id,
                    m.artifact.sha256,
                    m.artifact.size_bytes,
                )
                for m in proof.members
            ]
            if identities != expected or (rows and rows[-1][0].sample_id != proof.cursor):
                raise Conflict("Verified batch does not match the exact sealed membership")
            self._protect(session, group, [member.artifact.id for member in proof.members])
            require_current_access(session, actor.id, namespace_id, write=True)
            for member in proof.members:
                location = session.get(ArtifactLocationRow, member.artifact.location_id)
                if (
                    location is None
                    or location.artifact_id != member.artifact.id
                    or location.namespace_id != namespace_id
                    or location.store_id != self.store_id
                    or location.bucket != self.bucket
                    or location.object_key != member.artifact.object_key
                ):
                    raise Conflict("Artifact verification placement changed")
                reference = session.get(RetentionReferenceRow, (group.id, member.artifact.id))
                if reference.verified_at is None:
                    reference.verified_at = self._now(session)
            session.flush()
            group.prepared_count += len(proof.members)
            group.cursor = proof.cursor
            group.last_error = None
            self._record(
                session,
                actor,
                operation,
                publication_id=str(group.id),
                prepared_count=group.prepared_count,
            )
            session.flush()
            return self._view(session, group)

    def claim_manifest(self, actor, namespace_id, dataset_id, group_id):
        with self.sessions.begin() as session:
            group, operation = self._write_guards(
                session, actor, namespace_id, dataset_id, group_id
            )
            self._building(session, group, operation)
            if group.prepared_count != group.expected_count:
                raise Conflict("Finish bounded source verification before preparing the manifest")
            candidate = session.get(CandidateRow, group.candidate_id)
            report, policy = self._gate(
                session, namespace_id, dataset_id, candidate, group.report_id
            )
            require_current_access(session, actor.id, namespace_id, write=True)
            group.epoch += 1
            session.flush()
            return ManifestWork(
                group.id,
                group.epoch,
                group.expected_count,
                ManifestHeader(
                    namespace_id=namespace_id,
                    dataset_id=dataset_id,
                    candidate_id=candidate.id,
                    generation_id=group.generation_id,
                    source_revision=candidate.source_revision,
                    policy=QualityService._policy(policy).model_dump(mode="json"),
                    validation_report=QualityService._report(report).model_dump(mode="json"),
                ),
            )

    def manifest_members(self, actor, namespace_id, dataset_id, work: ManifestWork):
        after = None
        while True:
            with self.sessions.begin() as session:
                current_principal(session, actor.id).require(namespace_id, write=True)
                group = self._group(session, namespace_id, dataset_id, work.group_id)
                if (
                    group.state != "building"
                    or group.epoch != work.epoch
                    or group.expires_at <= self._now(session)
                ):
                    raise Conflict("Manifest preparation attempt is stale or expired")
                rows = self._members(session, group, after, 50)
                page = []
                for member, revision, binding, artifact in rows:
                    if binding is None or artifact is None:
                        raise Conflict("Frozen sample has no verified artifact binding")
                    page.append(
                        ManifestMember(
                            sample_id=member.sample_id,
                            sample_revision_id=revision.id,
                            split=member.split,
                            metadata=revision.metadata_json,
                            interpretation=InterpretationPins.model_validate(member.interpretation),
                            artifact=ManifestArtifact(
                                id=artifact.id,
                                sha256=artifact.sha256,
                                size_bytes=artifact.size_bytes,
                            ),
                        )
                    )
            yield from page
            if len(page) < 50:
                return
            after = page[-1].sample_id

    def allocate_manifest(
        self, actor, namespace_id, dataset_id, work: ManifestWork, digest: ObjectDigest
    ):
        """Trusted runner only; allocate and protect exact bytes BEFORE the external write."""
        with self.sessions.begin() as session:
            group, operation = self._write_guards(
                session, actor, namespace_id, dataset_id, work.group_id
            )
            self._building(session, group, operation)
            if group.epoch != work.epoch or group.prepared_count != group.expected_count:
                raise Conflict("Manifest preparation attempt is stale")
            if group.manifest_artifact_id is None:
                artifact = ArtifactRow(
                    namespace_id=namespace_id, sha256=digest.sha256, size_bytes=digest.size_bytes
                )
                session.add(artifact)
                session.flush()
                session.add(
                    ArtifactLocationRow(
                        namespace_id=namespace_id,
                        artifact_id=artifact.id,
                        store_id=self.store_id,
                        bucket=self.bucket,
                        object_key=f"namespaces/{namespace_id}/manifests/{group.id}",
                    )
                )
                session.flush()
                group.manifest_artifact_id = artifact.id
                group.manifest_digest, group.manifest_size = digest.sha256, digest.size_bytes
                session.flush()
            artifact = session.get(ArtifactRow, group.manifest_artifact_id)
            if artifact.sha256 != digest.sha256 or artifact.size_bytes != digest.size_bytes:
                raise Conflict("Manifest bytes differ from the persisted publication identity")
            self._protect(session, group, [artifact.id])
            require_current_access(session, actor.id, namespace_id, write=True)
            return self._source(session, namespace_id, artifact)

    def accept_manifest(
        self, actor, namespace_id, dataset_id, work: ManifestWork, source: ArtifactSource
    ):
        """Trusted runner only, after conditional upload and independent exact-byte readback."""
        with self.sessions.begin() as session:
            group, operation = self._write_guards(
                session, actor, namespace_id, dataset_id, work.group_id
            )
            self._building(session, group, operation)
            if group.epoch != work.epoch or group.manifest_artifact_id != source.id:
                raise Conflict("Manifest verification attempt is stale")
            artifact = session.scalar(
                select(ArtifactRow)
                .where(ArtifactRow.id == source.id, ArtifactRow.namespace_id == namespace_id)
                .with_for_update()
            )
            if (
                artifact is None
                or artifact.collection_state != "active"
                or source.digest != ObjectDigest(group.manifest_digest, group.manifest_size)
            ):
                raise Conflict("Protected manifest identity changed")
            location = session.get(ArtifactLocationRow, source.location_id)
            if (
                location is None
                or location.artifact_id != source.id
                or location.namespace_id != namespace_id
                or location.store_id != self.store_id
                or location.bucket != self.bucket
                or location.object_key != source.object_key
            ):
                raise Conflict("Manifest verification placement changed")
            require_current_access(session, actor.id, namespace_id, write=True)
            if artifact.verified_at is None:
                artifact.verified_at = self._now(session)
                session.flush([artifact])
            reference = session.get(RetentionReferenceRow, (group.id, source.id))
            if reference is None:
                raise Conflict("Manifest protection is unavailable")
            if reference.verified_at is None:
                reference.verified_at = self._now(session)
            session.flush()
            group.state, group.last_error = "prepared", None
            session.flush([group])
            self._record(
                session,
                actor,
                operation,
                publication_id=str(group.id),
                preparation="prepared",
                manifest_digest=group.manifest_digest,
            )
            return self._view(session, group)

    def record_error(self, actor, namespace_id, dataset_id, group_id, epoch, code):
        if code not in {
            "artifact_missing",
            "artifact_corrupt",
            "storage_unavailable",
            "manifest_too_large",
            "preparation_io_unavailable",
        }:
            raise ValueError("Unsupported preparation error")
        with self.sessions.begin() as session:
            group, operation = self._write_guards(
                session, actor, namespace_id, dataset_id, group_id
            )
            if group.state != "building" or group.epoch != epoch or operation.status != "running":
                return
            require_current_access(session, actor.id, namespace_id, write=True)
            group.last_error = code
            self._record(
                session, actor, operation, publication_id=str(group.id), preparation_error=code
            )

    @observed("publication")
    def finalize(self, actor, namespace_id, dataset_id, group_id):
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id, write=True)
            dataset = session.scalar(
                select(DatasetRow)
                .where(DatasetRow.id == dataset_id, DatasetRow.namespace_id == namespace_id)
                .with_for_update()
            )
            if dataset is None:
                raise NotFound("Dataset not found")
            draft = RegistryRepository(session).draft(namespace_id, dataset_id, lock=True)
            group, operation = self._write_guards(
                session, actor, namespace_id, dataset_id, group_id
            )
            if group.state == "published":
                return self._view(session, group)
            require_current_access(session, actor.id, namespace_id, write=True)
            if dataset.availability != "active":
                raise Conflict("Archived datasets pause publication; preparation is retained")
            if group.state != "prepared" or operation.status != "running":
                raise Conflict(
                    "Complete artifact protection and manifest preparation before publishing"
                )
            if group.expires_at <= self._now(session):
                raise Conflict("Preparation expired. Renew protection and recheck its artifacts")
            candidate = session.get(CandidateRow, group.candidate_id)
            if candidate.state != "sealed" or candidate.generation_id != group.generation_id:
                raise Conflict("Publication requires its exact sealed generation")
            report, _ = self._gate(session, namespace_id, dataset_id, candidate, group.report_id)
            if (
                operation.inputs["mode"] == "current_draft"
                and draft.revision != candidate.source_revision
            ):
                raise Conflict(
                    f"Selected candidate captures draft revision {candidate.source_revision}; "
                    f"latest draft is revision {draft.revision}. Preparation is retained."
                )
            label = operation.inputs["label"]
            prior = session.scalar(
                select(DatasetVersionRow.id).where(
                    DatasetVersionRow.namespace_id == namespace_id,
                    DatasetVersionRow.dataset_id == dataset_id,
                    DatasetVersionRow.label == label,
                )
            )
            if prior is not None:
                raise Conflict("Version label already exists; this preparation is retained")
            operation.status = "succeeded"
            session.flush([operation])
            group.state = "published"
            session.flush([group])
            version = DatasetVersionRow(
                id=uuid4(),
                namespace_id=namespace_id,
                dataset_id=dataset_id,
                label=label,
                candidate_id=candidate.id,
                generation_id=group.generation_id,
                report_id=report.id,
                group_id=group.id,
                manifest_artifact_id=group.manifest_artifact_id,
                manifest_digest=group.manifest_digest,
                manifest_size=group.manifest_size,
            )
            session.add(version)
            session.flush()
            payload = {
                "version_id": str(version.id),
                "publication_id": str(group.id),
                "candidate_id": str(candidate.id),
                "generation_id": str(group.generation_id),
                "report_id": str(report.id),
                "manifest_digest": group.manifest_digest,
            }
            self._record(session, actor, operation, **payload)
            RegistryService._audit(
                RegistryRepository(session),
                actor,
                namespace_id,
                version.id,
                "version.published",
                payload,
            )
            return self._view(session, group)

    @observed("publication")
    def renew(self, actor, namespace_id, dataset_id, group_id):
        with self.sessions.begin() as session:
            group, operation = self._write_guards(
                session, actor, namespace_id, dataset_id, group_id
            )
            now = self._now(session)
            require_current_access(session, actor.id, namespace_id, write=True)
            if group.state not in {"building", "prepared"} or operation.status != "running":
                raise Conflict("Released or published protection cannot be renewed")
            if group.expires_at > now:
                raise Conflict("Protection is still active; resume preparation instead")
            group.epoch += 1
            group.state, group.prepared_count, group.cursor = "building", 0, None
            group.expires_at = now + timedelta(hours=24)
            group.last_error = None
            session.flush([group])
            self._record(
                session, actor, operation, publication_id=str(group.id), preparation="renewed"
            )
            return self._view(session, group)

    @observed("publication")
    def release(self, actor, namespace_id, dataset_id, group_id, command: PublicationRelease):
        with self.sessions.begin() as session:
            group, operation = self._write_guards(
                session, actor, namespace_id, dataset_id, group_id
            )
            require_current_access(session, actor.id, namespace_id, write=True)
            return self._release(session, actor, group, operation, command.limit)

    @observed("publication")
    def expire(self, actor, namespace_id, dataset_id, group_id, command: PublicationRelease):
        """Managed expiry worker; the deadline check and release share the group guard."""
        with self.sessions.begin() as session:
            group, operation = self._write_guards(
                session, actor, namespace_id, dataset_id, group_id
            )
            require_current_access(session, actor.id, namespace_id, write=True)
            if group.state not in {"releasing", "released"} and group.expires_at > self._now(
                session
            ):
                raise Conflict("Publication protection has not expired")
            return self._release(session, actor, group, operation, command.limit)

    def _release(self, session, actor, group, operation, limit):
        if group.state == "published":
            raise Conflict("Published version protection cannot be released")
        if group.state == "released":
            return self._view(session, group)
        if group.state != "releasing":
            group.state, group.last_error = "releasing", None
            group.epoch += 1
            operation.status = "cancelled"
            session.flush([group])
            self._record(
                session, actor, operation, publication_id=str(group.id), preparation="releasing"
            )
        ids = session.scalars(
            select(RetentionReferenceRow.artifact_id)
            .where(RetentionReferenceRow.group_id == group.id)
            .order_by(RetentionReferenceRow.artifact_id)
            .limit(limit)
        ).all()
        if ids:
            session.execute(
                delete(RetentionReferenceRow).where(
                    RetentionReferenceRow.group_id == group.id,
                    RetentionReferenceRow.artifact_id.in_(ids),
                )
            )
        remaining = session.scalar(
            select(RetentionReferenceRow.artifact_id)
            .where(RetentionReferenceRow.group_id == group.id)
            .limit(1)
        )
        if remaining is None:
            group.state = "released"
        session.flush()
        self._audit(
            session,
            actor,
            group,
            "publication.references_released",
            count=len(ids),
            state=group.state,
        )
        return self._view(session, group)
