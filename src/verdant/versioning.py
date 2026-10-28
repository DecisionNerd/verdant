"""Frozen candidate capture over retained, revision-addressable draft membership."""

import hashlib
import json
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from verdant.contracts import Contract, Revision
from verdant.domain import Conflict, NotFound, Principal
from verdant.identity import current_principal
from verdant.persistence import (
    AuditRow,
    CandidateRow,
    DraftRow,
    GenerationMemberRow,
    RegistryRepository,
)
from verdant.telemetry import observed


class CandidateCapture(Contract):
    request_key: str = Field(min_length=1, max_length=200)
    expected_revision: Revision


class Candidate(Contract):
    id: UUID
    namespace_id: UUID
    dataset_id: UUID
    draft_id: UUID
    source_revision: int
    latest_draft_revision: int
    generation_id: UUID
    state: str
    prepared_count: int
    cursor: UUID | None
    created_at: datetime


class CandidatePrepare(Contract):
    expected_count: Revision
    limit: int = Field(default=100, ge=1, le=500, strict=True)


class VersioningService:
    def __init__(self, sessions):
        self.sessions = sessions

    @staticmethod
    def _view(row, draft):
        return Candidate(
            **{
                name: getattr(row, name)
                for name in Candidate.model_fields
                if name not in {"dataset_id", "latest_draft_revision"}
            },
            dataset_id=draft.dataset_id,
            latest_draft_revision=draft.revision,
        )

    @observed("versioning")
    def capture(
        self, actor: Principal, namespace_id: UUID, dataset_id: UUID, command: CandidateCapture
    ) -> Candidate:
        digest = hashlib.sha256(
            json.dumps(
                {"dataset_id": str(dataset_id), "source_revision": command.expected_revision},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id, write=True)
            draft = session.scalar(
                select(DraftRow)
                .where(DraftRow.namespace_id == namespace_id, DraftRow.dataset_id == dataset_id)
                .with_for_update()
            )
            if draft is None:
                raise NotFound("Dataset not found")
            prior = session.scalar(
                select(CandidateRow).where(
                    CandidateRow.namespace_id == namespace_id,
                    CandidateRow.creator_id == actor.id,
                    CandidateRow.request_key == command.request_key,
                )
            )
            if prior is not None:
                if prior.request_digest != digest:
                    raise Conflict("Capture request identity has different inputs")
                return self._view(prior, draft)
            if draft.revision != command.expected_revision:
                raise Conflict(
                    f"Draft changed from revision {command.expected_revision} to {draft.revision}"
                )
            candidate_id = session.scalar(
                insert(CandidateRow)
                .values(
                    id=uuid4(),
                    namespace_id=namespace_id,
                    draft_id=draft.id,
                    source_revision=draft.revision,
                    generation_id=uuid4(),
                    creator_id=actor.id,
                    request_key=command.request_key,
                    request_digest=digest,
                )
                .on_conflict_do_nothing(constraint="uq_candidate_request")
                .returning(CandidateRow.id)
            )
            if candidate_id is None:
                raise Conflict("Capture request identity has different inputs")
            session.add(
                AuditRow(
                    namespace_id=namespace_id,
                    actor_id=actor.id,
                    aggregate_id=candidate_id,
                    action="candidate.captured",
                    schema_version=1,
                    payload={
                        "transport": actor.transport,
                        "dataset_id": str(dataset_id),
                        "draft_id": str(draft.id),
                        "source_revision": draft.revision,
                    },
                )
            )
            return self._view(session.get(CandidateRow, candidate_id), draft)

    @observed("versioning")
    def prepare(
        self,
        actor: Principal,
        namespace_id: UUID,
        dataset_id: UUID,
        candidate_id: UUID,
        command: CandidatePrepare,
    ) -> Candidate:
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id, write=True)
            candidate = session.scalar(
                select(CandidateRow)
                .join(DraftRow, DraftRow.id == CandidateRow.draft_id)
                .where(
                    CandidateRow.id == candidate_id,
                    CandidateRow.namespace_id == namespace_id,
                    DraftRow.dataset_id == dataset_id,
                )
                .with_for_update(of=CandidateRow)
            )
            if candidate is None:
                raise NotFound("Candidate not found")
            draft = session.get(DraftRow, candidate.draft_id)
            if command.expected_count > candidate.prepared_count:
                raise Conflict("Candidate preparation cursor is ahead of committed progress")
            if command.expected_count < candidate.prepared_count or candidate.state == "sealed":
                return self._view(candidate, draft)
            members = RegistryRepository(session).membership_page(
                draft, candidate.source_revision, candidate.cursor, command.limit + 1
            )
            batch = members[: command.limit]
            for member in batch:
                session.add(
                    GenerationMemberRow(
                        namespace_id=namespace_id,
                        generation_id=candidate.generation_id,
                        sample_id=member.sample_id,
                        sample_revision_id=member.sample_revision_id,
                        split=member.split,
                        # Current registry supports unannotated, unreviewed, untransformed samples.
                        # Absence is pinned explicitly; it never resolves a future "latest" value.
                        interpretation={
                            "schema": "verdant.membership.v1",
                            "annotation_revision": None,
                            "task_schema_revision": None,
                            "review_evidence": None,
                            "transformation": None,
                        },
                    )
                )
            # INSERT guards lock the candidate and require preparing state. Flush before sealing.
            session.flush()
            candidate.prepared_count += len(batch)
            if batch:
                candidate.cursor = batch[-1].sample_id
            if len(members) <= command.limit:
                candidate.state = "sealed"
            session.add(
                AuditRow(
                    namespace_id=namespace_id,
                    actor_id=actor.id,
                    aggregate_id=candidate.id,
                    action="candidate.prepared",
                    schema_version=1,
                    payload={
                        "transport": actor.transport,
                        "source_revision": candidate.source_revision,
                        "generation_id": str(candidate.generation_id),
                        "prepared_count": candidate.prepared_count,
                        "state": candidate.state,
                    },
                )
            )
            return self._view(candidate, draft)
