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


def test_effect_success_survives_new_attempt_without_new_dispatch(registry, sessions):
    from verdant.operations import EffectRequest, EffectResult

    _, actor, ns = registry
    service = OperationService(sessions)
    op = service.admit(
        actor, ns, OperationRequest(request_key="effect-success", kind="qualification")
    )
    service.start(actor, ns, op.id, 1)
    command = EffectRequest(
        kind="provider-write", item="item-1", input_revision="rev-1", request={"value": 1}
    )
    receipt = service.prepare_effect(actor, ns, op.id, 1, command)
    dispatch = service.begin_effect(actor, ns, op.id, 1, receipt.id)
    result = EffectResult(result_digest="a" * 64, provider_reference="provider-result-1")
    completed = service.complete_effect(
        actor, ns, op.id, 1, receipt.id, dispatch.dispatch_id, result
    )
    assert (
        service.complete_effect(actor, ns, op.id, 1, receipt.id, dispatch.dispatch_id, result)
        == completed
    )
    service.fail(actor, ns, op.id, 1)
    service.retry(actor, ns, op.id, 1)
    service.start(actor, ns, op.id, 2)
    reused = service.prepare_effect(actor, ns, op.id, 2, command)
    assert reused == completed
    with pytest.raises(Conflict):
        service.begin_effect(actor, ns, op.id, 2, receipt.id)
    with pytest.raises(Conflict):
        service.prepare_effect(
            actor, ns, op.id, 2, command.model_copy(update={"request": {"value": 2}})
        )


def test_provider_success_then_crash_requires_reconciliation_before_replay(registry, sessions):
    from verdant.operations import EffectReconciliation, EffectRequest, EffectResult

    _, actor, ns = registry
    service = OperationService(sessions)
    op = service.admit(actor, ns, OperationRequest(request_key="ambiguous", kind="qualification"))
    service.start(actor, ns, op.id, 1)
    command = EffectRequest(kind="provider-write", item="item", input_revision="rev", request={})
    receipt = service.prepare_effect(actor, ns, op.id, 1, command)
    dispatch = service.begin_effect(actor, ns, op.id, 1, receipt.id)
    # Model a provider write that succeeds, followed by process death before receipt completion.
    provider_writes = ["provider-record"]
    service.fail(actor, ns, op.id, 1)
    service.retry(actor, ns, op.id, 1)
    service.start(actor, ns, op.id, 2)
    unknown = service.prepare_effect(actor, ns, op.id, 2, command)
    assert unknown.id == receipt.id and unknown.status == "unknown"
    with pytest.raises(Conflict):
        service.begin_effect(actor, ns, op.id, 2, receipt.id)
    result = EffectResult(result_digest="b" * 64, provider_reference=provider_writes[0])
    with pytest.raises(Conflict, match="stale"):
        service.complete_effect(actor, ns, op.id, 1, receipt.id, dispatch.dispatch_id, result)
    reconciled = service.reconcile_effect(
        actor,
        ns,
        op.id,
        receipt.id,
        EffectReconciliation(
            expected_revision=unknown.revision,
            outcome="found",
            result=result,
            evidence="Provider lookup matched stable invocation key",
        ),
    )
    assert reconciled.status == "succeeded"
    assert len(provider_writes) == 1
    assert service.prepare_effect(actor, ns, op.id, 2, command).id == reconciled.id


def test_unknown_effect_blocks_cancellation_until_authorized_reconciliation(registry, sessions):
    from verdant.operations import EffectReconciliation, EffectRequest

    _, actor, ns = registry
    service = OperationService(sessions)
    op = service.admit(
        actor, ns, OperationRequest(request_key="cancel-effect", kind="qualification")
    )
    service.start(actor, ns, op.id, 1)
    receipt = service.prepare_effect(
        actor, ns, op.id, 1, EffectRequest(kind="write", item="item", input_revision="1")
    )
    unknown = service.begin_effect(actor, ns, op.id, 1, receipt.id)
    service.request_cancel(actor, ns, op.id, 1)
    with pytest.raises(Conflict, match="reconciled"):
        service.confirm_cancel(actor, ns, op.id, 1)
    service.reconcile_effect(
        actor,
        ns,
        op.id,
        receipt.id,
        EffectReconciliation(
            expected_revision=unknown.revision,
            outcome="absent",
            evidence="Provider lookup proved no accepted write",
        ),
    )
    assert service.confirm_cancel(actor, ns, op.id, 1).status == "cancelled"


def prepared_verification(registry, sessions, result_digest=None):
    from verdant.contracts import SampleCreate
    from verdant.operations import EffectRequest, EffectResult

    registry_service, actor, ns = registry
    revision = registry_service.create_sample(
        actor,
        ns,
        SampleCreate(
            source_key="verify",
            modality="text",
            sha256="a" * 64,
            object_key="synthetic/verify",
            size_bytes=1,
        ),
    )
    service = OperationService(sessions)
    op = service.admit(
        actor,
        ns,
        OperationRequest(
            request_key="verification",
            kind="verify_sample_artifact",
            inputs={"sample_id": str(revision.sample_id), "revision_id": str(revision.id)},
        ),
    )
    service.start(actor, ns, op.id, 1)
    with pytest.raises(Conflict):
        service.finalize_verification(actor, ns, op.id, 1)
    receipt = service.prepare_effect(
        actor,
        ns,
        op.id,
        1,
        EffectRequest(kind="artifact.read", item=str(revision.id), input_revision=revision.sha256),
    )
    dispatch = service.begin_effect(actor, ns, op.id, 1, receipt.id)
    service.complete_effect(
        actor,
        ns,
        op.id,
        1,
        receipt.id,
        dispatch.dispatch_id,
        EffectResult(
            result_digest=result_digest or revision.sha256, provider_reference=str(revision.id)
        ),
    )
    return service, actor, ns, op, revision


def test_success_requires_accepted_result_and_commits_it_atomically(registry, sessions):
    from verdant.persistence import VerificationResultRow

    service, actor, ns, op, revision = prepared_verification(registry, sessions)
    finished = service.finalize_verification(actor, ns, op.id, 1)
    assert finished.status == "succeeded"
    assert service.finalize_verification(actor, ns, op.id, 1) == finished
    with sessions() as session:
        accepted = session.get(VerificationResultRow, op.id)
        assert accepted.sample_revision_id == revision.id
        assert accepted.sha256 == revision.sha256
        event = session.scalar(
            select(OperationEventRow).where(
                OperationEventRow.operation_id == op.id,
                OperationEventRow.event_type == "operation.succeeded",
            )
        )
        assert session.get(OutboxRow, event.id)
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditRow)
                .where(AuditRow.aggregate_id == op.id, AuditRow.action == "operation.succeeded")
            )
            == 1
        )
    with pytest.raises(Conflict):
        service.fail(actor, ns, op.id, 1)


def test_cancellation_and_success_finalization_have_one_serialized_outcome(registry, sessions):
    from verdant.persistence import VerificationResultRow

    service, actor, ns, op, _ = prepared_verification(registry, sessions)
    barrier = Barrier(2)

    def action(cancel):
        barrier.wait(timeout=5)
        try:
            return (service.request_cancel if cancel else service.finalize_verification)(
                actor, ns, op.id, 1
            ).status
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(action, [True, False]))
    state = service.get(actor, ns, op.id).status
    assert outcomes.count("conflict") == 1
    with sessions() as session:
        accepted = session.get(VerificationResultRow, op.id)
        assert (accepted is not None) == (state == "succeeded")
    assert state in {"succeeded", "cancellation_requested"}


def test_wrong_artifact_digest_cannot_finalize_success(registry, sessions):
    service, actor, ns, op, _ = prepared_verification(registry, sessions, result_digest="b" * 64)
    with pytest.raises(Conflict, match="matching artifact result"):
        service.finalize_verification(actor, ns, op.id, 1)
    assert service.get(actor, ns, op.id).status == "running"


def test_duplicate_inbox_delivery_has_one_dispatch_and_atomic_checkpoint(registry, sessions):
    from verdant.persistence import ConsumerCheckpointRow, ConsumerInboxRow, WorkflowDispatchRow

    _, actor, ns = registry
    service = OperationService(sessions)
    op = service.admit(actor, ns, OperationRequest(request_key="inbox", kind="qualification"))
    with sessions() as session:
        queued = session.scalar(
            select(OperationEventRow).where(OperationEventRow.operation_id == op.id)
        )
        event_id = queued.id
    barrier = Barrier(2)

    def consume(_):
        barrier.wait(timeout=5)
        return service.receive_workflow_event(actor, ns, event_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(consume, range(2)))
    assert outcomes[0] == outcomes[1]
    assert outcomes[0].outcome == "applied"
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(WorkflowDispatchRow)
                .where(WorkflowDispatchRow.operation_id == op.id)
            )
            == 1
        )
        assert session.get(ConsumerInboxRow, ("workflow-dispatch", event_id))
        assert session.get(ConsumerCheckpointRow, ("workflow-dispatch", op.id)).revision == 0


def test_unsupported_event_is_quarantined_without_advancing_checkpoint(registry, sessions):
    from verdant.persistence import ConsumerCheckpointRow, ConsumerInboxRow

    _, actor, ns = registry
    service = OperationService(sessions)
    op = service.admit(actor, ns, OperationRequest(request_key="unsupported", kind="qualification"))
    event_id = uuid4()
    with sessions.begin() as session:
        session.add(
            OperationEventRow(
                id=event_id,
                namespace_id=ns,
                operation_id=op.id,
                revision=10,
                event_type="operation.future",
                schema_version=2,
                payload={},
            )
        )
    result = service.receive_workflow_event(actor, ns, event_id)
    assert result.outcome == "quarantined"
    assert result.error
    with sessions() as session:
        assert session.get(ConsumerInboxRow, ("workflow-dispatch", event_id)).error
        assert session.get(ConsumerCheckpointRow, ("workflow-dispatch", op.id)) is None


def test_out_of_order_events_do_not_regress_checkpoint_or_dispatch_old_attempt(registry, sessions):
    from verdant.persistence import ConsumerCheckpointRow

    _, actor, ns = registry
    service = OperationService(sessions)
    op = service.admit(actor, ns, OperationRequest(request_key="ordered", kind="qualification"))
    service.start(actor, ns, op.id, 1)
    service.fail(actor, ns, op.id, 1)
    service.retry(actor, ns, op.id, 1)
    with sessions() as session:
        events = session.scalars(
            select(OperationEventRow)
            .where(OperationEventRow.operation_id == op.id)
            .order_by(OperationEventRow.revision)
        ).all()
    assert service.receive_workflow_event(actor, ns, events[0].id).outcome == "stale"
    assert service.receive_workflow_event(actor, ns, events[3].id).dispatch.epoch == 2
    assert service.receive_workflow_event(actor, ns, events[1].id).outcome == "ignored"
    with sessions() as session:
        assert session.get(ConsumerCheckpointRow, ("workflow-dispatch", op.id)).revision == 3


def test_inbox_dispatch_and_checkpoint_roll_back_together_on_audit_failure(registry, sessions):
    from verdant.persistence import ConsumerCheckpointRow, ConsumerInboxRow, WorkflowDispatchRow

    _, actor, ns = registry
    service = OperationService(sessions)
    op = service.admit(
        actor, ns, OperationRequest(request_key="inbox-rollback", kind="qualification")
    )
    with sessions() as session:
        event_id = session.scalar(
            select(OperationEventRow.id).where(OperationEventRow.operation_id == op.id)
        )

    def fail(*_):
        raise RuntimeError("inbox audit unavailable")

    event.listen(AuditRow, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="inbox audit"):
            service.receive_workflow_event(actor, ns, event_id)
    finally:
        event.remove(AuditRow, "before_insert", fail)
    with sessions() as session:
        assert session.get(ConsumerInboxRow, ("workflow-dispatch", event_id)) is None
        assert session.get(ConsumerCheckpointRow, ("workflow-dispatch", op.id)) is None
        assert session.get(WorkflowDispatchRow, (op.id, 1)) is None
    assert service.receive_workflow_event(actor, ns, event_id).outcome == "applied"


def dispatch_fixture(registry, sessions):
    _, actor, ns = registry
    service = OperationService(sessions)
    op = service.admit(
        actor, ns, OperationRequest(request_key="workflow-submit", kind="qualification")
    )
    with sessions() as session:
        event_id = session.scalar(
            select(OperationEventRow.id).where(OperationEventRow.operation_id == op.id)
        )
    dispatch = service.receive_workflow_event(actor, ns, event_id).dispatch
    return service, actor, ns, op, dispatch


def test_workflow_unknown_admission_requires_reconciliation_before_cancel(registry, sessions):
    from verdant.operations import WorkflowReconciliation

    service, actor, ns, op, pending = dispatch_fixture(registry, sessions)
    unknown = service.begin_workflow_submission(actor, ns, op.id, 1)
    assert unknown.status == "unknown"
    assert unknown.invocation_key == pending.invocation_key
    with pytest.raises(Conflict):
        service.begin_workflow_submission(actor, ns, op.id, 1)
    service.request_cancel(actor, ns, op.id, 1)
    with pytest.raises(Conflict, match="workflow admission"):
        service.confirm_cancel(actor, ns, op.id, 1)
    provider_id = uuid4()
    reconciled = service.reconcile_workflow_submission(
        actor,
        ns,
        op.id,
        1,
        WorkflowReconciliation(
            request_digest=unknown.request_digest,
            provider_run_id=provider_id,
            evidence="Provider lookup matched stable idempotency key",
        ),
    )
    assert reconciled.status == "submitted"
    assert reconciled.provider_reference == str(provider_id)
    assert service.confirm_cancel(actor, ns, op.id, 1).status == "cancelled"


def test_workflow_submission_result_is_idempotent_and_epoch_fenced(registry, sessions):
    from verdant.operations import WorkflowReconciliation

    service, actor, ns, op, _ = dispatch_fixture(registry, sessions)
    unknown = service.begin_workflow_submission(actor, ns, op.id, 1)
    provider_id = uuid4()
    service.fail(actor, ns, op.id, 1)
    service.retry(actor, ns, op.id, 1)
    with pytest.raises(Conflict, match="stale"):
        service.complete_workflow_submission(actor, ns, op.id, 1, provider_id)
    # Authorized reconciliation records the old provider fact without changing the new attempt.
    reconciled = service.reconcile_workflow_submission(
        actor,
        ns,
        op.id,
        1,
        WorkflowReconciliation(
            request_digest=unknown.request_digest,
            provider_run_id=provider_id,
            evidence="Late provider lookup",
        ),
    )
    assert reconciled.provider_reference == str(provider_id)
    assert service.get(actor, ns, op.id).epoch == 2
    assert service.get(actor, ns, op.id).status == "queued"
