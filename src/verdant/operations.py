"""PostgreSQL owns logical operation identity and status; orchestration is not authority."""

import hashlib
import json
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, model_validator
from sqlalchemy import func, or_, select
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


class DeliveryClaim(Contract):
    event_id: UUID
    claim_id: UUID
    operation_id: UUID
    event_type: str
    schema_version: int
    payload: dict[str, JsonValue]
    lease_until: datetime


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

    @observed("operations")
    def claim_delivery(
        self, actor: Principal, namespace_id: UUID, limit: int = 25
    ) -> list[DeliveryClaim]:
        if not 1 <= limit <= 100:
            raise ValueError("Claim batch must be between 1 and 100")
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id, write=True)
            now = session.scalar(select(func.clock_timestamp()))
            rows = session.execute(
                select(OutboxRow, OperationEventRow)
                .join(OperationEventRow, OperationEventRow.id == OutboxRow.event_id)
                .where(
                    OperationEventRow.namespace_id == namespace_id,
                    OutboxRow.dispatched.is_(False),
                    or_(OutboxRow.lease_until.is_(None), OutboxRow.lease_until <= now),
                )
                .order_by(OperationEventRow.created_at, OperationEventRow.id)
                .with_for_update(skip_locked=True, of=OutboxRow)
                .limit(limit)
            ).all()
            claims = []
            for row, event in rows:
                row.claim_id = uuid4()
                row.lease_until = now + timedelta(seconds=30)
                row.attempts += 1
                claims.append(
                    DeliveryClaim(
                        event_id=event.id,
                        claim_id=row.claim_id,
                        operation_id=event.operation_id,
                        event_type=event.event_type,
                        schema_version=event.schema_version,
                        payload=event.payload,
                        lease_until=row.lease_until,
                    )
                )
                session.add(
                    AuditRow(
                        namespace_id=namespace_id,
                        actor_id=actor.id,
                        aggregate_id=event.operation_id,
                        action="operation.delivery_claimed",
                        schema_version=1,
                        payload={
                            "transport": actor.transport,
                            "event_id": str(event.id),
                            "claim_id": str(row.claim_id),
                            "attempt": row.attempts,
                        },
                    )
                )
            return claims

    @observed("operations")
    def acknowledge_delivery(
        self, actor: Principal, namespace_id: UUID, event_id: UUID, claim_id: UUID
    ) -> None:
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id, write=True)
            found = session.execute(
                select(OutboxRow, OperationEventRow)
                .join(OperationEventRow, OperationEventRow.id == OutboxRow.event_id)
                .where(
                    OperationEventRow.namespace_id == namespace_id, OutboxRow.event_id == event_id
                )
                .with_for_update(of=OutboxRow)
            ).first()
            if found is None:
                raise NotFound("Delivery not found")
            row, event = found
            if row.claim_id != claim_id:
                raise Conflict("Delivery claim is stale")
            if row.dispatched:
                return
            now = session.scalar(select(func.clock_timestamp()))
            if row.lease_until is None or row.lease_until <= now:
                raise Conflict("Delivery claim expired")
            row.dispatched = True
            session.add(
                AuditRow(
                    namespace_id=namespace_id,
                    actor_id=actor.id,
                    aggregate_id=event.operation_id,
                    action="operation.delivery_acknowledged",
                    schema_version=1,
                    payload={
                        "transport": actor.transport,
                        "event_id": str(event_id),
                        "claim_id": str(claim_id),
                    },
                )
            )
