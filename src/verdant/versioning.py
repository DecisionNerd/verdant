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
from verdant.persistence import AuditRow, CandidateRow, DraftRow
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
    created_at: datetime


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
