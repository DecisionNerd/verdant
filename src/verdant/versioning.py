"""Frozen candidate capture over retained, revision-addressable draft membership."""

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from verdant.contracts import Contract, InterpretationPins, Revision
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


class CandidatePage(Contract):
    items: list[Candidate]
    next_after: UUID | None = None


class GenerationMember(Contract):
    sample_id: UUID
    sample_revision_id: UUID
    split: str
    interpretation: dict


class GenerationPage(Contract):
    candidate: Candidate
    items: list[GenerationMember]
    next_after: UUID | None = None


class CandidateSelection(Contract):
    mode: Literal["selected_candidate", "current_draft"]


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
                        # Copy the captured interval's exact contract, never current evidence.
                        interpretation=InterpretationPins.model_validate(
                            member.interpretation
                        ).model_dump(by_alias=True),
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

    @staticmethod
    def _candidate_query(namespace_id, dataset_id, candidate_id):
        return (
            select(CandidateRow, DraftRow)
            .join(DraftRow, DraftRow.id == CandidateRow.draft_id)
            .where(
                CandidateRow.namespace_id == namespace_id,
                DraftRow.dataset_id == dataset_id,
                CandidateRow.id == candidate_id,
            )
        )

    @observed("versioning")
    def get(
        self, actor: Principal, namespace_id: UUID, dataset_id: UUID, candidate_id: UUID
    ) -> Candidate:
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id)
            result = session.execute(
                self._candidate_query(namespace_id, dataset_id, candidate_id)
            ).one_or_none()
            if result is None:
                raise NotFound("Candidate not found")
            return self._view(*result)

    @observed("versioning")
    def list_candidates(
        self,
        actor: Principal,
        namespace_id: UUID,
        dataset_id: UUID,
        after: UUID | None = None,
        limit: int = 25,
    ) -> CandidatePage:
        if not 1 <= limit <= 100:
            raise ValueError("Candidate page must be between 1 and 100")
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id)
            draft = RegistryRepository(session).draft(namespace_id, dataset_id)
            if draft is None:
                raise NotFound("Dataset not found")
            query = select(CandidateRow).where(
                CandidateRow.draft_id == draft.id, CandidateRow.namespace_id == namespace_id
            )
            if after is not None:
                query = query.where(CandidateRow.id > after)
            rows = session.scalars(query.order_by(CandidateRow.id).limit(limit + 1)).all()
            return CandidatePage(
                items=[self._view(row, draft) for row in rows[:limit]],
                next_after=rows[limit - 1].id if len(rows) > limit else None,
            )

    @observed("versioning")
    def members(
        self,
        actor: Principal,
        namespace_id: UUID,
        dataset_id: UUID,
        candidate_id: UUID,
        after: UUID | None = None,
        limit: int = 25,
    ) -> GenerationPage:
        if not 1 <= limit <= 100:
            raise ValueError("Generation page must be between 1 and 100")
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id)
            result = session.execute(
                self._candidate_query(namespace_id, dataset_id, candidate_id).with_for_update(
                    read=True, of=CandidateRow
                )
            ).one_or_none()
            if result is None:
                raise NotFound("Candidate not found")
            candidate, draft = result
            query = select(GenerationMemberRow).where(
                GenerationMemberRow.namespace_id == namespace_id,
                GenerationMemberRow.generation_id == candidate.generation_id,
            )
            if after is not None:
                query = query.where(GenerationMemberRow.sample_id > after)
            rows = session.scalars(
                query.order_by(GenerationMemberRow.sample_id).limit(limit + 1)
            ).all()
            return GenerationPage(
                candidate=self._view(candidate, draft),
                items=[
                    GenerationMember(
                        **{name: getattr(row, name) for name in GenerationMember.model_fields}
                    )
                    for row in rows[:limit]
                ],
                next_after=rows[limit - 1].sample_id if len(rows) > limit else None,
            )

    @staticmethod
    def require_selection(candidate, draft, mode):
        if candidate.state != "sealed":
            raise Conflict("Candidate preparation must finish before selecting it for publication")
        if mode == "current_draft" and candidate.source_revision != draft.revision:
            raise Conflict(
                f"Selected candidate captures draft revision {candidate.source_revision}; "
                f"latest draft is revision {draft.revision}. Preparation is retained. "
                "Use the selected candidate or capture the current draft."
            )

    @observed("versioning")
    def check_selection(
        self,
        actor: Principal,
        namespace_id: UUID,
        dataset_id: UUID,
        candidate_id: UUID,
        command: CandidateSelection,
    ) -> Candidate:
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id, write=True)
            # Lock order matches capture: draft before candidate. This guard can be reused
            # inside actual short publication finalization when that owning command exists.
            draft = RegistryRepository(session).draft(namespace_id, dataset_id, lock=True)
            if draft is None:
                raise NotFound("Dataset not found")
            candidate = session.scalar(
                select(CandidateRow)
                .where(
                    CandidateRow.namespace_id == namespace_id,
                    CandidateRow.draft_id == draft.id,
                    CandidateRow.id == candidate_id,
                )
                .with_for_update()
            )
            if candidate is None:
                raise NotFound("Candidate not found")
            self.require_selection(candidate, draft, command.mode)
            return self._view(candidate, draft)
