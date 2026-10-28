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
    ConsumerCheckpointRow,
    ConsumerInboxRow,
    EffectReceiptRow,
    NamespaceRow,
    OperationEventRow,
    OperationRow,
    OutboxRow,
    SampleRevisionRow,
    VerificationResultRow,
    WorkflowDispatchRow,
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


class WorkflowDispatch(Contract):
    operation_id: UUID
    epoch: int
    event_id: UUID
    invocation_key: str
    request_digest: str
    status: str
    deployment_id: UUID | None
    provider_reference: str | None


class InboxOutcome(Contract):
    outcome: str
    error: str | None = None
    dispatch: WorkflowDispatch | None = None


class VerificationSource(Contract):
    sample_id: UUID
    revision_id: UUID
    object_key: str
    sha256: str
    size_bytes: int


class WorkflowReconciliation(Contract):
    request_digest: Digest
    provider_run_id: UUID | None = None
    evidence: str = Field(min_length=1, max_length=2000)


class VerificationRequest(Contract):
    request_key: str = Field(min_length=1, max_length=200)
    sample_id: UUID
    revision_id: UUID


class OperationAction(Contract):
    epoch: int = Field(ge=1, le=9223372036854775807, strict=True)


class OperationPage(Contract):
    items: list[Operation]
    next_after: UUID | None = None


class EffectSummary(Contract):
    id: UUID
    kind: str
    status: str
    revision: int
    result_digest: str | None


class OperationDetail(Contract):
    operation: Operation
    needs_reconciliation: bool
    provider_run_id: UUID | None = None
    accepted_digest: str | None = None
    effects: list[EffectSummary]
    next_effect_after: UUID | None = None


class OperationService:
    def __init__(self, sessions):
        self.sessions = sessions

    @staticmethod
    def _view(row):
        return Operation(**{name: getattr(row, name) for name in Operation.model_fields})

    @observed("operations")
    def admit(self, actor: Principal, namespace_id: UUID, command: OperationRequest) -> Operation:
        with self.sessions.begin() as session:
            return self._admit(session, actor, namespace_id, command)

    def _admit(
        self,
        session,
        actor: Principal,
        namespace_id: UUID,
        command: OperationRequest,
        *,
        initial_status: str = "queued",
    ):
        """Compose admission with owning-domain rows in one caller-owned transaction."""
        if initial_status not in {"queued", "running"}:
            raise ValueError("Unsupported initial operation state")
        digest = hashlib.sha256(command.canonical().encode()).hexdigest()
        current_principal(session, actor.id).require(namespace_id, write=True)
        if session.get(NamespaceRow, namespace_id) is None:
            raise NotFound("Namespace not found")
        if command.kind == "verify_sample_artifact":
            try:
                sample_id, revision_id = (
                    UUID(command.inputs["sample_id"]),
                    UUID(command.inputs["revision_id"]),
                )
            except (KeyError, TypeError, ValueError):
                raise Conflict("Verification request has invalid sample identity") from None
            source = session.scalar(
                select(SampleRevisionRow.id).where(
                    SampleRevisionRow.namespace_id == namespace_id,
                    SampleRevisionRow.sample_id == sample_id,
                    SampleRevisionRow.id == revision_id,
                )
            )
            if source is None:
                raise NotFound("Verification sample revision not found")
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
                status=initial_status,
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
                event_type="operation." + initial_status,
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
            if (
                target == "cancelled"
                and session.scalar(
                    select(WorkflowDispatchRow.operation_id)
                    .where(
                        WorkflowDispatchRow.operation_id == operation_id,
                        WorkflowDispatchRow.epoch == epoch,
                        WorkflowDispatchRow.status == "unknown",
                    )
                    .limit(1)
                )
                is not None
            ):
                raise Conflict("Unknown workflow admission must be reconciled before cancellation")
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
    def cancel_verification(
        self, actor: Principal, namespace_id: UUID, operation_id: UUID, epoch: int
    ):
        """Cancel the read-only verifier; lock also fences concurrent finalization."""
        with self.sessions.begin() as session:
            row = self._locked_operation(session, actor, namespace_id, operation_id, epoch)
            if row.kind != "verify_sample_artifact":
                raise Conflict("Unsupported verification operation kind")
            if row.status == "cancelled":
                return self._view(row)
            if row.status != "cancellation_requested":
                raise Conflict("Verification cancellation has not been requested")
            dispatch = session.get(WorkflowDispatchRow, (operation_id, epoch))
            if dispatch is not None and dispatch.status == "unknown":
                raise Conflict("Unknown workflow admission must be reconciled before cancellation")
            revision_id = UUID(row.inputs["revision_id"])
            revision = session.get(SampleRevisionRow, revision_id)
            if revision is None or revision.namespace_id != namespace_id:
                raise Conflict("Verification revision is unavailable")
            receipts = session.scalars(
                select(EffectReceiptRow).where(EffectReceiptRow.operation_id == operation_id)
            ).all()
            expected = {
                "kind": "artifact.read",
                "item": str(revision_id),
                "input_revision": revision.sha256,
            }
            if len(receipts) > 1 or any(receipt.identity != expected for receipt in receipts):
                raise Conflict("Verification has unexpected effects")
            for receipt in receipts:
                if receipt.status == "unknown":
                    # This exact operation can only read immutable bytes. No write is replayed.
                    receipt.status = "prepared"
                    receipt.revision += 1
                    receipt.dispatch_id = None
                    receipt.dispatch_epoch = None
                    row.revision += 1
                    self._record_transition(
                        session,
                        actor,
                        row,
                        "operation.effect_reconciled",
                        {
                            "effect_id": str(receipt.id),
                            "outcome": "absent",
                            "evidence": "Read-only verification cancelled before result acceptance",
                        },
                    )
            row.status = "cancelled"
            row.revision += 1
            self._record_transition(session, actor, row)
            return self._view(row)

    @observed("operations")
    def request_reconcile(
        self, actor: Principal, namespace_id: UUID, operation_id: UUID, epoch: int
    ):
        with self.sessions.begin() as session:
            row = self._locked_operation(session, actor, namespace_id, operation_id, epoch)
            if row.kind != "verify_sample_artifact" or row.status in {"succeeded", "cancelled"}:
                raise Conflict("Operation does not permit reconciliation")
            pending = session.scalar(
                select(OperationEventRow.id)
                .join(OutboxRow, OutboxRow.event_id == OperationEventRow.id)
                .where(
                    OperationEventRow.operation_id == operation_id,
                    OperationEventRow.event_type == "operation.reconciliation_requested",
                    OperationEventRow.payload["epoch"].as_integer() == epoch,
                    OutboxRow.dispatched.is_(False),
                )
                .limit(1)
            )
            if pending is None:
                row.revision += 1
                self._record_transition(session, actor, row, "operation.reconciliation_requested")
            return self._view(row)

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
            return self._reconcile_effect(
                session, actor, namespace_id, operation_id, receipt_id, command
            )

    def _reconcile_effect(self, session, actor, namespace_id, operation_id, receipt_id, command):
        """Compose verified provider facts with an owning domain's atomic acceptance."""
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
    def verification_source(
        self, actor: Principal, namespace_id: UUID, operation_id: UUID, epoch: int
    ) -> VerificationSource:
        with self.sessions.begin() as session:
            operation = self._locked_operation(session, actor, namespace_id, operation_id, epoch)
            if operation.kind != "verify_sample_artifact":
                raise Conflict("Unsupported verification operation kind")
            try:
                sample_id, revision_id = (
                    UUID(operation.inputs["sample_id"]),
                    UUID(operation.inputs["revision_id"]),
                )
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
            return VerificationSource(
                sample_id=sample_id,
                revision_id=revision_id,
                object_key=revision.object_key,
                sha256=revision.sha256,
                size_bytes=revision.size_bytes,
            )

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

    @staticmethod
    def _dispatch_view(row):
        if row is None:
            return None
        return WorkflowDispatch(
            **{name: getattr(row, name) for name in WorkflowDispatch.model_fields}
        )

    @observed("operations")
    def receive_workflow_event(
        self, actor: Principal, namespace_id: UUID, event_id: UUID
    ) -> InboxOutcome:
        consumer = "workflow-dispatch"
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id, write=True)
            event = session.scalar(
                select(OperationEventRow).where(
                    OperationEventRow.namespace_id == namespace_id, OperationEventRow.id == event_id
                )
            )
            if event is None:
                raise NotFound("Event not found")
            operation = self._locked_operation(session, actor, namespace_id, event.operation_id)
            prior = session.get(ConsumerInboxRow, (consumer, event_id))
            if prior is not None:
                dispatch = session.scalar(
                    select(WorkflowDispatchRow).where(WorkflowDispatchRow.event_id == event_id)
                )
                return InboxOutcome(
                    outcome=prior.outcome, error=prior.error, dispatch=self._dispatch_view(dispatch)
                )
            known = {
                "operation." + value
                for value in (
                    "queued",
                    "running",
                    "cancellation_requested",
                    "cancelled",
                    "failed",
                    "succeeded",
                    "effect_prepared",
                    "effect_dispatched",
                    "effect_completed",
                    "effect_reconciled",
                    "workflow_submission_started",
                    "workflow_submitted",
                    "workflow_reconciled",
                    "reconciliation_requested",
                    "upload_ready",
                    "upload_failed",
                    "ingestion_item_accepted",
                )
            }
            error = None
            if event.schema_version != 1 or event.event_type not in known:
                error = "Unsupported workflow event type or schema version"
            elif (
                event.payload.get("operation_id") != str(operation.id)
                or type(event.payload.get("epoch")) is not int
                or not 1 <= event.payload["epoch"] <= operation.epoch
            ):
                error = "Invalid workflow event identity or epoch"
            outcome, dispatch = "ignored", None
            if error is not None:
                outcome = "quarantined"
            elif event.event_type == "operation.queued":
                if event.payload["epoch"] != operation.epoch or operation.status != "queued":
                    outcome = "stale"
                else:
                    invocation = f"verdant:{namespace_id}:{operation.id}:{operation.epoch}"
                    digest = hashlib.sha256(
                        (invocation + ":" + operation.request_digest).encode()
                    ).hexdigest()
                    dispatch = WorkflowDispatchRow(
                        operation_id=operation.id,
                        epoch=operation.epoch,
                        namespace_id=namespace_id,
                        event_id=event_id,
                        invocation_key=invocation,
                        request_digest=digest,
                    )
                    session.add(dispatch)
                    outcome = "applied"
            session.add(
                ConsumerInboxRow(consumer=consumer, event_id=event_id, outcome=outcome, error=error)
            )
            if error is None:
                checkpoint = session.get(ConsumerCheckpointRow, (consumer, operation.id))
                if checkpoint is None:
                    session.add(
                        ConsumerCheckpointRow(
                            consumer=consumer,
                            operation_id=operation.id,
                            namespace_id=namespace_id,
                            revision=event.revision,
                        )
                    )
                else:
                    checkpoint.revision = max(checkpoint.revision, event.revision)
            session.add(
                AuditRow(
                    namespace_id=namespace_id,
                    actor_id=actor.id,
                    aggregate_id=operation.id,
                    action="operation.event_consumed",
                    schema_version=1,
                    payload={
                        "transport": actor.transport,
                        "event_id": str(event_id),
                        "consumer": consumer,
                        "outcome": outcome,
                    },
                )
            )
            session.flush()
            return InboxOutcome(
                outcome=outcome, error=error, dispatch=self._dispatch_view(dispatch)
            )

    @observed("operations")
    def quarantine_workflow_kind(self, actor: Principal, namespace_id: UUID, event_id: UUID):
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id, write=True)
            event = session.get(OperationEventRow, event_id)
            if event is None or event.namespace_id != namespace_id:
                raise NotFound("Event not found")
            operation = self._locked_operation(session, actor, namespace_id, event.operation_id)
            if operation.kind == "verify_sample_artifact":
                raise Conflict("Supported verification kind cannot be quarantined")
            inbox = session.get(ConsumerInboxRow, ("workflow-dispatch", event_id))
            if inbox is None:
                raise Conflict("Event has not been consumed")
            dispatch = session.scalar(
                select(WorkflowDispatchRow).where(WorkflowDispatchRow.event_id == event_id)
            )
            if dispatch is not None and dispatch.status != "pending":
                raise Conflict("Started provider admission must be reconciled")
            if inbox.outcome != "quarantined":
                inbox.outcome = "quarantined"
                inbox.error = "Unsupported operation kind for artifact verification deployment"
                session.add(
                    AuditRow(
                        namespace_id=namespace_id,
                        actor_id=actor.id,
                        aggregate_id=operation.id,
                        action="operation.workflow_quarantined",
                        schema_version=1,
                        payload={"transport": actor.transport, "event_id": str(event_id)},
                    )
                )

    @staticmethod
    def _locked_dispatch(session, operation_id, epoch):
        row = session.scalar(
            select(WorkflowDispatchRow)
            .where(
                WorkflowDispatchRow.operation_id == operation_id, WorkflowDispatchRow.epoch == epoch
            )
            .with_for_update()
        )
        if row is None:
            raise NotFound("Workflow dispatch not found")
        return row

    @observed("operations")
    def workflow_dispatch(
        self, actor: Principal, namespace_id: UUID, operation_id: UUID, epoch: int
    ) -> WorkflowDispatch:
        with self.sessions.begin() as session:
            self._locked_operation(session, actor, namespace_id, operation_id)
            return self._dispatch_view(self._locked_dispatch(session, operation_id, epoch))

    @observed("operations")
    def begin_workflow_submission(
        self,
        actor: Principal,
        namespace_id: UUID,
        operation_id: UUID,
        epoch: int,
        deployment_id: UUID,
    ) -> WorkflowDispatch:
        with self.sessions.begin() as session:
            operation = self._locked_operation(session, actor, namespace_id, operation_id, epoch)
            row = self._locked_dispatch(session, operation_id, epoch)
            if row.deployment_id is not None and row.deployment_id != deployment_id:
                raise Conflict("Workflow deployment differs from the persisted dispatch")
            if row.status == "submitted":
                return self._dispatch_view(row)
            if row.status != "pending" or operation.status != "queued":
                raise Conflict("Workflow dispatch must reconcile before submission")
            if row.deployment_id is None:
                row.deployment_id = deployment_id
                row.request_digest = hashlib.sha256(
                    json.dumps(
                        {"request_digest": row.request_digest, "deployment_id": str(deployment_id)},
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
            row.status = "unknown"
            operation.revision += 1
            self._record_transition(
                session,
                actor,
                operation,
                "operation.workflow_submission_started",
                {"dispatch_epoch": epoch, "invocation_key": row.invocation_key},
            )
            return self._dispatch_view(row)

    @observed("operations")
    def complete_workflow_submission(
        self,
        actor: Principal,
        namespace_id: UUID,
        operation_id: UUID,
        epoch: int,
        provider_run_id: UUID,
    ) -> WorkflowDispatch:
        with self.sessions.begin() as session:
            operation = self._locked_operation(session, actor, namespace_id, operation_id, epoch)
            row = self._locked_dispatch(session, operation_id, epoch)
            reference = str(provider_run_id)
            if row.status == "submitted":
                if row.provider_reference != reference:
                    raise Conflict("Workflow result conflicts with committed dispatch")
                return self._dispatch_view(row)
            if row.status != "unknown":
                raise Conflict("Workflow dispatch has not started")
            row.status, row.provider_reference = "submitted", reference
            operation.revision += 1
            self._record_transition(
                session,
                actor,
                operation,
                "operation.workflow_submitted",
                {"dispatch_epoch": epoch, "provider_run_id": reference},
            )
            return self._dispatch_view(row)

    @observed("operations")
    def reconcile_workflow_submission(
        self,
        actor: Principal,
        namespace_id: UUID,
        operation_id: UUID,
        epoch: int,
        command: WorkflowReconciliation,
    ) -> WorkflowDispatch:
        with self.sessions.begin() as session:
            operation = self._locked_operation(session, actor, namespace_id, operation_id)
            row = self._locked_dispatch(session, operation_id, epoch)
            if row.request_digest != command.request_digest or row.status != "unknown":
                raise Conflict("Workflow reconciliation is stale or mismatched")
            row.provider_reference = (
                str(command.provider_run_id) if command.provider_run_id else None
            )
            row.status = "submitted" if command.provider_run_id else "pending"
            operation.revision += 1
            self._record_transition(
                session,
                actor,
                operation,
                "operation.workflow_reconciled",
                {
                    "dispatch_epoch": epoch,
                    "provider_run_id": row.provider_reference,
                    "evidence": command.evidence,
                },
            )
            return self._dispatch_view(row)

    @observed("operations")
    def admit_verification(
        self, actor: Principal, namespace_id: UUID, command: VerificationRequest
    ) -> Operation:
        return self.admit(
            actor,
            namespace_id,
            OperationRequest(
                request_key=command.request_key,
                kind="verify_sample_artifact",
                inputs={
                    "sample_id": str(command.sample_id),
                    "revision_id": str(command.revision_id),
                },
            ),
        )

    @observed("operations")
    def list_operations(
        self, actor: Principal, namespace_id: UUID, after: UUID | None = None, limit: int = 25
    ) -> OperationPage:
        if not 1 <= limit <= 100:
            raise ValueError("Operation page must be between 1 and 100")
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id)
            query = select(OperationRow).where(OperationRow.namespace_id == namespace_id)
            if after is not None:
                query = query.where(OperationRow.id > after)
            rows = session.scalars(query.order_by(OperationRow.id).limit(limit + 1)).all()
            return OperationPage(
                items=[self._view(row) for row in rows[:limit]],
                next_after=rows[limit - 1].id if len(rows) > limit else None,
            )

    @observed("operations")
    def detail(
        self,
        actor: Principal,
        namespace_id: UUID,
        operation_id: UUID,
        after: UUID | None = None,
        limit: int = 25,
    ) -> OperationDetail:
        if not 1 <= limit <= 100:
            raise ValueError("Effect page must be between 1 and 100")
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id)
            row = session.scalar(
                select(OperationRow).where(
                    OperationRow.namespace_id == namespace_id, OperationRow.id == operation_id
                )
            )
            if row is None:
                raise NotFound("Operation not found")
            effects_query = select(EffectReceiptRow).where(
                EffectReceiptRow.operation_id == operation_id
            )
            if after is not None:
                effects_query = effects_query.where(EffectReceiptRow.id > after)
            effects = session.scalars(
                effects_query.order_by(EffectReceiptRow.id).limit(limit + 1)
            ).all()
            unknown = session.scalar(
                select(EffectReceiptRow.id)
                .where(
                    EffectReceiptRow.operation_id == operation_id,
                    EffectReceiptRow.status == "unknown",
                )
                .limit(1)
            )
            dispatch = session.get(WorkflowDispatchRow, (operation_id, row.epoch))
            accepted = session.get(VerificationResultRow, operation_id)
            return OperationDetail(
                operation=self._view(row),
                needs_reconciliation=unknown is not None
                or (dispatch is not None and dispatch.status == "unknown"),
                provider_run_id=UUID(dispatch.provider_reference)
                if dispatch and dispatch.provider_reference
                else None,
                accepted_digest=accepted.sha256 if accepted else None,
                effects=[
                    EffectSummary(
                        id=effect.id,
                        kind=effect.identity["kind"],
                        status=effect.status,
                        revision=effect.revision,
                        result_digest=effect.result_digest,
                    )
                    for effect in effects[:limit]
                ],
                next_effect_after=effects[limit - 1].id if len(effects) > limit else None,
            )
