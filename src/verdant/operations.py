"""PostgreSQL owns logical operation identity and status; orchestration is not authority."""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert

from verdant.contracts import Contract, Digest, Revision
from verdant.domain import Conflict, NotFound, Principal
from verdant.identity import current_principal
from verdant.persistence import (
    AuditRow,
    EffectReceiptRow,
    NamespaceRow,
    OperationEventRow,
    OperationRow,
    OutboxRow,
    SampleRevisionRow,
    VerificationResultRow,
)
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


class EffectRequest(Contract):
    kind: str = Field(min_length=1, max_length=100)
    item: str = Field(min_length=1, max_length=500)
    input_revision: str = Field(min_length=1, max_length=200)
    request: dict[str, JsonValue] = Field(default_factory=dict)

    def identity(self):
        # Attempt epoch is deliberately absent from logical effect identity.
        return self.model_dump(exclude={"request"})

    @model_validator(mode="after")
    def bounded(self):
        if len(json.dumps(self.model_dump(), ensure_ascii=False, allow_nan=False).encode()) > 65536:
            raise ValueError("Effect input exceeds 64 KiB")
        return self


class EffectReceipt(Contract):
    id: UUID
    operation_id: UUID
    effect_key: str
    request_digest: str
    status: str
    revision: int
    dispatch_id: UUID | None
    dispatch_epoch: int | None
    result_digest: str | None
    provider_reference: str | None


class EffectResult(Contract):
    result_digest: Digest
    provider_reference: str = Field(min_length=1, max_length=1000)


class EffectReconciliation(Contract):
    expected_revision: Revision
    outcome: Literal["found", "absent"]
    result: EffectResult | None = None
    evidence: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def matching_result(self):
        if (self.outcome == "found") != (self.result is not None):
            raise ValueError("Found reconciliation requires a result; absent must omit it")
        return self


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

    @staticmethod
    def _record_transition(
        session, actor: Principal, row: OperationRow, event_type=None, extra=None
    ):
        event_id = uuid4()
        event_type = event_type or "operation." + row.status
        payload = {"operation_id": str(row.id), "epoch": row.epoch, "revision": row.revision}
        payload |= extra or {}
        session.add(
            OperationEventRow(
                id=event_id,
                namespace_id=row.namespace_id,
                operation_id=row.id,
                revision=row.revision,
                event_type=event_type,
                schema_version=1,
                payload=payload,
            )
        )
        session.flush()
        session.add(OutboxRow(event_id=event_id))
        session.add(
            AuditRow(
                namespace_id=row.namespace_id,
                actor_id=actor.id,
                aggregate_id=row.id,
                action=event_type,
                schema_version=1,
                payload=payload | {"transport": actor.transport, "event_id": str(event_id)},
            )
        )

    @observed("operations")
    def get(self, actor: Principal, namespace_id: UUID, operation_id: UUID) -> Operation:
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id)
            row = session.scalar(
                select(OperationRow).where(
                    OperationRow.namespace_id == namespace_id, OperationRow.id == operation_id
                )
            )
            if row is None:
                raise NotFound("Operation not found")
            return self._view(row)

    def _transition(
        self,
        actor: Principal,
        namespace_id: UUID,
        operation_id: UUID,
        epoch: int,
        allowed: set[str],
        target: str,
        *,
        new_attempt: bool = False,
    ) -> Operation:
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id, write=True)
            row = session.scalar(
                select(OperationRow)
                .where(OperationRow.namespace_id == namespace_id, OperationRow.id == operation_id)
                .with_for_update()
            )
            if row is None:
                raise NotFound("Operation not found")
            if row.epoch != epoch:
                raise Conflict("Operation attempt is stale")
            if row.status == target and not new_attempt:
                return self._view(row)
            if row.status not in allowed:
                raise Conflict("Operation state does not permit this transition")
            if row.revision == 9223372036854775807 or (
                new_attempt and row.epoch == 9223372036854775807
            ):
                raise Conflict("Operation revision limit reached")
            if (
                target == "cancelled"
                and session.scalar(
                    select(EffectReceiptRow.id)
                    .where(
                        EffectReceiptRow.operation_id == operation_id,
                        EffectReceiptRow.status == "unknown",
                    )
                    .limit(1)
                )
                is not None
            ):
                raise Conflict("Unknown effects must be reconciled before cancellation completes")
            row.status = target
            row.revision += 1
            if new_attempt:
                row.epoch += 1
            self._record_transition(session, actor, row)
            return self._view(row)

    @observed("operations")
    def start(self, actor: Principal, namespace_id: UUID, operation_id: UUID, epoch: int):
        return self._transition(actor, namespace_id, operation_id, epoch, {"queued"}, "running")

    @observed("operations")
    def request_cancel(self, actor: Principal, namespace_id: UUID, operation_id: UUID, epoch: int):
        return self._transition(
            actor,
            namespace_id,
            operation_id,
            epoch,
            {"queued", "running"},
            "cancellation_requested",
        )

    @observed("operations")
    def confirm_cancel(self, actor: Principal, namespace_id: UUID, operation_id: UUID, epoch: int):
        return self._transition(
            actor, namespace_id, operation_id, epoch, {"cancellation_requested"}, "cancelled"
        )

    @observed("operations")
    def fail(self, actor: Principal, namespace_id: UUID, operation_id: UUID, epoch: int):
        return self._transition(
            actor,
            namespace_id,
            operation_id,
            epoch,
            {"queued", "running", "cancellation_requested"},
            "failed",
        )

    @observed("operations")
    def retry(self, actor: Principal, namespace_id: UUID, operation_id: UUID, epoch: int):
        return self._transition(
            actor, namespace_id, operation_id, epoch, {"failed"}, "queued", new_attempt=True
        )

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

    @staticmethod
    def _effect_view(row):
        return EffectReceipt(**{name: getattr(row, name) for name in EffectReceipt.model_fields})

    @staticmethod
    def _locked_operation(session, actor, namespace_id, operation_id, epoch=None):
        current_principal(session, actor.id).require(namespace_id, write=True)
        operation = session.scalar(
            select(OperationRow)
            .where(OperationRow.namespace_id == namespace_id, OperationRow.id == operation_id)
            .with_for_update()
        )
        if operation is None:
            raise NotFound("Operation not found")
        if epoch is not None and operation.epoch != epoch:
            raise Conflict("Operation attempt is stale")
        return operation

    @staticmethod
    def _locked_effect(session, namespace_id, operation_id, receipt_id):
        row = session.scalar(
            select(EffectReceiptRow)
            .where(
                EffectReceiptRow.namespace_id == namespace_id,
                EffectReceiptRow.operation_id == operation_id,
                EffectReceiptRow.id == receipt_id,
            )
            .with_for_update()
        )
        if row is None:
            raise NotFound("Effect receipt not found")
        return row

    def _effect_event(self, session, actor, operation, receipt, action):
        operation.revision += 1
        self._record_transition(
            session,
            actor,
            operation,
            "operation.effect_" + action,
            {"receipt_id": str(receipt.id), "effect_key": receipt.effect_key},
        )

    @observed("operations")
    def prepare_effect(
        self,
        actor: Principal,
        namespace_id: UUID,
        operation_id: UUID,
        epoch: int,
        command: EffectRequest,
    ) -> EffectReceipt:
        def digest(value):
            return hashlib.sha256(
                json.dumps(
                    value,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode()
            ).hexdigest()

        key, request_digest = digest(command.identity()), digest(command.request)
        with self.sessions.begin() as session:
            operation = self._locked_operation(session, actor, namespace_id, operation_id, epoch)
            if operation.status != "running":
                raise Conflict("Effects require a running operation")
            row = session.scalar(
                select(EffectReceiptRow).where(
                    EffectReceiptRow.operation_id == operation_id,
                    EffectReceiptRow.effect_key == key,
                )
            )
            if row is not None:
                if row.request_digest != request_digest:
                    raise Conflict("Logical effect was already bound to different inputs")
                return self._effect_view(row)
            row = EffectReceiptRow(
                namespace_id=namespace_id,
                operation_id=operation_id,
                effect_key=key,
                identity=command.identity(),
                request_digest=request_digest,
                request=command.request,
            )
            session.add(row)
            session.flush()
            self._effect_event(session, actor, operation, row, "prepared")
            return self._effect_view(row)

    @observed("operations")
    def begin_effect(
        self, actor: Principal, namespace_id: UUID, operation_id: UUID, epoch: int, receipt_id: UUID
    ) -> EffectReceipt:
        with self.sessions.begin() as session:
            operation = self._locked_operation(session, actor, namespace_id, operation_id, epoch)
            if operation.status != "running":
                raise Conflict("Effects require a running operation")
            row = self._locked_effect(session, namespace_id, operation_id, receipt_id)
            if row.status != "prepared":
                raise Conflict("Effect already dispatched; reuse success or reconcile uncertainty")
            # Persist uncertainty before external I/O. A crash must never allow blind replay.
            row.status, row.dispatch_id, row.dispatch_epoch = "unknown", uuid4(), epoch
            row.revision += 1
            self._effect_event(session, actor, operation, row, "dispatched")
            return self._effect_view(row)

    @observed("operations")
    def complete_effect(
        self,
        actor: Principal,
        namespace_id: UUID,
        operation_id: UUID,
        epoch: int,
        receipt_id: UUID,
        dispatch_id: UUID,
        result: EffectResult,
    ) -> EffectReceipt:
        with self.sessions.begin() as session:
            operation = self._locked_operation(session, actor, namespace_id, operation_id, epoch)
            row = self._locked_effect(session, namespace_id, operation_id, receipt_id)
            if row.dispatch_id != dispatch_id or row.dispatch_epoch != epoch:
                raise Conflict("Effect dispatch is stale")
            if row.status == "succeeded":
                if (row.result_digest, row.provider_reference) != (
                    result.result_digest,
                    result.provider_reference,
                ):
                    raise Conflict("Effect result conflicts with its committed receipt")
                return self._effect_view(row)
            if (
                operation.status not in {"running", "cancellation_requested"}
                or row.status != "unknown"
            ):
                raise Conflict("Effect result requires reconciliation")
            row.status = "succeeded"
            row.result_digest, row.provider_reference = (
                result.result_digest,
                result.provider_reference,
            )
            row.revision += 1
            self._effect_event(session, actor, operation, row, "completed")
            return self._effect_view(row)

    @observed("operations")
    def reconcile_effect(
        self,
        actor: Principal,
        namespace_id: UUID,
        operation_id: UUID,
        receipt_id: UUID,
        command: EffectReconciliation,
    ) -> EffectReceipt:
        with self.sessions.begin() as session:
            # A current authorized reconciler may record provider facts from an old epoch.
            operation = self._locked_operation(session, actor, namespace_id, operation_id)
            row = self._locked_effect(session, namespace_id, operation_id, receipt_id)
            if row.status != "unknown" or row.revision != command.expected_revision:
                raise Conflict("Effect reconciliation is stale")
            if command.outcome == "found":
                row.status = "succeeded"
                row.result_digest = command.result.result_digest
                row.provider_reference = command.result.provider_reference
            else:
                row.status = "prepared"
                row.dispatch_id = row.dispatch_epoch = None
            row.reconciliation_evidence = command.evidence
            row.revision += 1
            self._effect_event(session, actor, operation, row, "reconciled")
            return self._effect_view(row)

    @observed("operations")
    def finalize_verification(
        self, actor: Principal, namespace_id: UUID, operation_id: UUID, epoch: int
    ) -> Operation:
        with self.sessions.begin() as session:
            operation = self._locked_operation(session, actor, namespace_id, operation_id, epoch)
            if operation.status == "succeeded":
                return self._view(operation)
            if operation.status != "running" or operation.kind != "verify_sample_artifact":
                raise Conflict("Operation does not permit verification finalization")
            try:
                sample_id = UUID(operation.inputs["sample_id"])
                revision_id = UUID(operation.inputs["revision_id"])
            except (KeyError, TypeError, ValueError):
                raise Conflict("Verification request has invalid sample identity") from None
            revision = session.scalar(
                select(SampleRevisionRow).where(
                    SampleRevisionRow.namespace_id == namespace_id,
                    SampleRevisionRow.sample_id == sample_id,
                    SampleRevisionRow.id == revision_id,
                )
            )
            if revision is None:
                raise NotFound("Verification sample revision not found")
            # Artifact verification accepts exactly one matching read receipt.
            receipts = session.scalars(
                select(EffectReceiptRow)
                .where(EffectReceiptRow.operation_id == operation_id)
                .limit(2)
            ).all()
            expected_identity = {
                "kind": "artifact.read",
                "item": str(revision_id),
                "input_revision": revision.sha256,
            }
            if len(receipts) != 1 or receipts[0].identity != expected_identity:
                raise Conflict("Verification requires its exact artifact-read receipt")
            receipt = receipts[0]
            if (
                receipt.status != "succeeded"
                or receipt.result_digest != revision.sha256
                or receipt.provider_reference != str(revision_id)
            ):
                raise Conflict("Verification has no accepted matching artifact result")
            session.add(
                VerificationResultRow(
                    operation_id=operation_id,
                    namespace_id=namespace_id,
                    sample_id=sample_id,
                    sample_revision_id=revision_id,
                    sha256=revision.sha256,
                    size_bytes=revision.size_bytes,
                    epoch=epoch,
                )
            )
            operation.status = "succeeded"
            operation.revision += 1
            self._record_transition(
                session,
                actor,
                operation,
                extra={"sample_revision_id": str(revision_id), "sha256": revision.sha256},
            )
            return self._view(operation)
