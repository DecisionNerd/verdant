from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select

from verdant.domain import Conflict, Forbidden
from verdant.operations import OperationRequest, OperationService
from verdant.persistence import AuditRow, OperationEventRow, OperationRow, OutboxRow


def test_concurrent_admission_commits_one_operation_event_outbox_and_audit(registry, sessions):
    _, actor, ns = registry
    service = OperationService(sessions)
    command = OperationRequest(request_key=str(uuid4()), kind="qualification", inputs={"item": 1})
    barrier = Barrier(2)

    def admit(_):
        barrier.wait(timeout=5)
        return service.admit(actor, ns, command)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(admit, range(2)))
    assert outcomes[0] == outcomes[1]
    assert outcomes[0].status == "queued"
    with sessions() as session:
        events = session.scalars(
            select(OperationEventRow).where(OperationEventRow.operation_id == outcomes[0].id)
        ).all()
        assert len(events) == 1
        assert session.get(OutboxRow, events[0].id) is not None
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditRow)
                .where(AuditRow.aggregate_id == outcomes[0].id)
            )
            == 1
        )
    with pytest.raises(Conflict):
        service.admit(actor, ns, command.model_copy(update={"inputs": {"item": 2}}))


def test_audit_failure_rolls_back_admission_and_delivery(registry, sessions):
    _, actor, ns = registry
    service = OperationService(sessions)
    command = OperationRequest(request_key=str(uuid4()), kind="qualification")

    def fail(*_):
        raise RuntimeError("injected audit write failure")

    event.listen(AuditRow, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            service.admit(actor, ns, command)
    finally:
        event.remove(AuditRow, "before_insert", fail)
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(OperationRow)
                .where(OperationRow.namespace_id == ns)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(OperationEventRow)
                .where(OperationEventRow.namespace_id == ns)
            )
            == 0
        )


def test_admission_requires_current_write_access(registry, principal_factory, sessions):
    _, actor, ns = registry
    service = OperationService(sessions)
    denied = principal_factory(ns, access="read")
    with pytest.raises(Forbidden):
        service.admit(denied, ns, OperationRequest(request_key="denied", kind="qualification"))


def test_competing_delivery_claims_and_stale_acknowledgement_are_fenced(registry, sessions):
    from datetime import timedelta

    _, actor, ns = registry
    service = OperationService(sessions)
    service.admit(actor, ns, OperationRequest(request_key="claim", kind="qualification"))
    barrier = Barrier(2)

    def claim(_):
        barrier.wait(timeout=5)
        return service.claim_delivery(actor, ns, limit=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        batches = list(pool.map(claim, range(2)))
    claims = [item for batch in batches for item in batch]
    assert len(claims) == 1
    original = claims[0]
    with sessions.begin() as session:
        session.get(OutboxRow, original.event_id).lease_until = session.scalar(
            select(func.clock_timestamp())
        ) - timedelta(seconds=1)
    replacement = service.claim_delivery(actor, ns, limit=1)[0]
    assert replacement.event_id == original.event_id
    assert replacement.claim_id != original.claim_id
    with pytest.raises(Conflict):
        service.acknowledge_delivery(actor, ns, original.event_id, original.claim_id)
    service.acknowledge_delivery(actor, ns, replacement.event_id, replacement.claim_id)
    service.acknowledge_delivery(actor, ns, replacement.event_id, replacement.claim_id)
    assert service.claim_delivery(actor, ns) == []


def test_revocation_stops_new_claims_and_acknowledgement(registry, sessions):
    from sqlalchemy import delete

    from verdant.persistence import NamespaceGrantRow

    _, actor, ns = registry
    service = OperationService(sessions)
    service.admit(actor, ns, OperationRequest(request_key="revoke", kind="qualification"))
    claim = service.claim_delivery(actor, ns)[0]
    with sessions.begin() as session:
        session.execute(delete(NamespaceGrantRow).where(NamespaceGrantRow.principal_id == actor.id))
    with pytest.raises(Forbidden):
        service.claim_delivery(actor, ns)
    with pytest.raises(Forbidden):
        service.acknowledge_delivery(actor, ns, claim.event_id, claim.claim_id)


def test_retry_increments_epoch_and_stale_attempt_cannot_change_state(registry, sessions):
    _, actor, ns = registry
    service = OperationService(sessions)
    op = service.admit(actor, ns, OperationRequest(request_key="epochs", kind="qualification"))
    running = service.start(actor, ns, op.id, 1)
    assert running.status == "running"
    assert service.start(actor, ns, op.id, 1) == running
    failed = service.fail(actor, ns, op.id, 1)
    assert failed.status == "failed"
    retried = service.retry(actor, ns, op.id, 1)
    assert retried.epoch == 2
    assert retried.status == "queued"
    with pytest.raises(Conflict, match="stale"):
        service.fail(actor, ns, op.id, 1)
    assert service.get(actor, ns, op.id) == retried
    with sessions() as session:
        events = session.scalars(
            select(OperationEventRow)
            .where(OperationEventRow.operation_id == op.id)
            .order_by(OperationEventRow.revision)
        ).all()
        assert [event.revision for event in events] == [0, 1, 2, 3]
        assert all(session.get(OutboxRow, event.id) for event in events)


def test_cancellation_and_start_race_serializes_on_operation(registry, sessions):
    _, actor, ns = registry
    service = OperationService(sessions)
    op = service.admit(actor, ns, OperationRequest(request_key="cancel-race", kind="qualification"))
    barrier = Barrier(2)

    def action(cancel):
        barrier.wait(timeout=5)
        try:
            return (service.request_cancel if cancel else service.start)(actor, ns, op.id, 1).status
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(action, [True, False]))
    assert service.get(actor, ns, op.id).status == "cancellation_requested"
    assert service.confirm_cancel(actor, ns, op.id, 1).status == "cancelled"
    with pytest.raises(Conflict):
        service.start(actor, ns, op.id, 1)
    with pytest.raises(Conflict):
        service.retry(actor, ns, op.id, 1)
