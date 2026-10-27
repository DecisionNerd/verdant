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
