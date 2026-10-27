"""PostgreSQL owns logical operation identity and status; orchestration is not authority."""

import hashlib
import json
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from verdant.contracts import Contract
from verdant.domain import Conflict, NotFound, Principal
from verdant.identity import current_principal
from verdant.persistence import AuditRow, NamespaceRow, OperationEventRow, OperationRow, OutboxRow
from verdant.telemetry import observed


class OperationRequest(Contract):
    request_key: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.-]*$")
    inputs: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded(self):
        if len(self.canonical().encode()) > 65536:
            raise ValueError("Operation input exceeds 64 KiB")
        return self

    def canonical(self):
        return json.dumps(
            {"kind": self.kind, "inputs": self.inputs},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )


class Operation(Contract):
    id: UUID
    namespace_id: UUID
    creator_id: UUID
    kind: str
    status: str
    epoch: int
    revision: int
    created_at: datetime


class OperationService:
    def __init__(self, sessions):
        self.sessions = sessions

    @staticmethod
    def _view(row):
        return Operation(**{name: getattr(row, name) for name in Operation.model_fields})

    @observed("operations")
    def admit(self, actor: Principal, namespace_id: UUID, command: OperationRequest) -> Operation:
        digest = hashlib.sha256(command.canonical().encode()).hexdigest()
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id, write=True)
            if session.get(NamespaceRow, namespace_id) is None:
                raise NotFound("Namespace not found")
            operation_id = session.scalar(
                insert(OperationRow)
                .values(
                    id=uuid4(),
                    namespace_id=namespace_id,
                    creator_id=actor.id,
                    request_key=command.request_key,
                    request_digest=digest,
                    kind=command.kind,
                    inputs=command.inputs,
                )
                .on_conflict_do_nothing(constraint="uq_operation_request")
                .returning(OperationRow.id)
            )
            if operation_id is None:
                existing = session.scalar(
                    select(OperationRow).where(
                        OperationRow.namespace_id == namespace_id,
                        OperationRow.creator_id == actor.id,
                        OperationRow.request_key == command.request_key,
                    )
                )
                if existing.request_digest != digest:
                    raise Conflict("Operation request key was already used for different inputs")
                return self._view(existing)
            event_id = uuid4()
            session.add(
                OperationEventRow(
                    id=event_id,
                    namespace_id=namespace_id,
                    operation_id=operation_id,
                    revision=0,
                    event_type="operation.queued",
                    schema_version=1,
                    payload={"operation_id": str(operation_id), "epoch": 1},
                )
            )
            session.flush()
            session.add(OutboxRow(event_id=event_id))
            session.add(
                AuditRow(
                    namespace_id=namespace_id,
                    actor_id=actor.id,
                    aggregate_id=operation_id,
                    action="operation.admitted",
                    schema_version=1,
                    payload={"transport": actor.transport, "event_id": str(event_id), "epoch": 1},
                )
            )
            return self._view(session.get(OperationRow, operation_id))
