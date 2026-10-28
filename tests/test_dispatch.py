import asyncio
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

pytest.importorskip("prefect")

from verdant.contracts import SampleCreate
from verdant.dispatch import WorkflowDispatcher
from verdant.operations import OperationService, VerificationRequest
from verdant.persistence import OperationEventRow, OutboxRow


class LostResponseProvider:
    def __init__(self, deployment):
        self.deployment = deployment
        self.runs = []
        self.creates = 0
        self.lookups = 0

    async def create_flow_run_from_deployment(self, deployment, *, parameters, idempotency_key):
        assert deployment == self.deployment
        self.creates += 1
        self.runs.append(
            SimpleNamespace(
                id=uuid4(),
                deployment_id=deployment,
                parameters=parameters,
                idempotency_key=idempotency_key,
            )
        )
        raise TimeoutError("simulated response loss after provider success")

    async def read_flow_runs(self, **kwargs):
        self.lookups += 1
        return self.runs


def test_dispatch_redelivery_reconciles_provider_success_without_duplicate_create(
    registry, sessions
):
    registry_service, actor, ns = registry
    revision = registry_service.create_sample(
        actor,
        ns,
        SampleCreate(
            source_key="dispatch",
            modality="text",
            sha256="a" * 64,
            object_key="private/dispatch",
            size_bytes=1,
        ),
    )
    operations = OperationService(sessions)
    op = operations.admit_verification(
        actor,
        ns,
        VerificationRequest(
            request_key="dispatch", sample_id=revision.sample_id, revision_id=revision.id
        ),
    )
    deployment = uuid4()
    provider = LostResponseProvider(deployment)
    dispatcher = WorkflowDispatcher(sessions, provider, deployment)
    first = asyncio.run(dispatcher.tick(actor, ns, limit=1))
    assert first.claimed == first.deferred == 1
    assert first.acknowledged == 0
    assert operations.workflow_dispatch(actor, ns, op.id, 1).status == "unknown"
    with sessions.begin() as session:
        rows = session.scalars(
            select(OutboxRow)
            .join(OperationEventRow, OperationEventRow.id == OutboxRow.event_id)
            .where(OperationEventRow.namespace_id == ns, OutboxRow.claim_id.is_not(None))
        ).all()
        for row in rows:
            row.lease_until = session.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
    second = asyncio.run(dispatcher.tick(actor, ns, limit=100))
    assert second.deferred == 0
    dispatch = operations.workflow_dispatch(actor, ns, op.id, 1)
    assert dispatch.status == "submitted"
    assert dispatch.provider_reference == str(provider.runs[0].id)
    assert provider.creates == provider.lookups == 1
    asyncio.run(dispatcher.tick(actor, ns, limit=100))
    assert provider.creates == 1


def test_dispatch_revocation_denies_claim_before_provider_call(registry, sessions):
    from sqlalchemy import delete

    from verdant.domain import Forbidden
    from verdant.operations import OperationRequest
    from verdant.persistence import NamespaceGrantRow

    _, actor, ns = registry
    OperationService(sessions).admit(
        actor, ns, OperationRequest(request_key="denied-dispatch", kind="qualification")
    )
    deployment = uuid4()
    provider = LostResponseProvider(deployment)
    with sessions.begin() as session:
        session.execute(delete(NamespaceGrantRow).where(NamespaceGrantRow.principal_id == actor.id))
    with pytest.raises(Forbidden):
        asyncio.run(WorkflowDispatcher(sessions, provider, deployment).tick(actor, ns))
    assert provider.creates == provider.lookups == 0


@pytest.mark.parametrize(
    "provider_accepted, lookup_found", [(False, False), (True, True), (True, False)]
)
def test_cancelled_verification_never_creates_another_provider_run(
    registry, sessions, provider_accepted, lookup_found
):
    registry_service, actor, ns = registry
    revision = registry_service.create_sample(
        actor,
        ns,
        SampleCreate(
            source_key="cancel",
            modality="text",
            sha256="b" * 64,
            object_key="private/cancel",
            size_bytes=1,
        ),
    )
    operations = OperationService(sessions)
    op = operations.admit_verification(
        actor,
        ns,
        VerificationRequest(
            request_key="cancel", sample_id=revision.sample_id, revision_id=revision.id
        ),
    )
    deployment = uuid4()
    provider = LostResponseProvider(deployment)
    dispatcher = WorkflowDispatcher(sessions, provider, deployment)
    if provider_accepted:
        assert asyncio.run(dispatcher.tick(actor, ns, limit=1)).deferred == 1
    if provider_accepted and not lookup_found:
        provider.runs.clear()  # Provider double proves absence; cancellation must not reissue.
    operations.request_cancel(actor, ns, op.id, 1)
    batch = asyncio.run(dispatcher.tick(actor, ns, limit=100))
    assert batch.deferred == 0
    assert operations.get(actor, ns, op.id).status == "cancelled"
    assert provider.creates == int(provider_accepted)
    assert provider.lookups == int(provider_accepted)


def test_reconciliation_of_provider_completion_requires_domain_acceptance(registry, sessions):
    from prefect.states import Completed

    registry_service, actor, ns = registry
    revision = registry_service.create_sample(
        actor,
        ns,
        SampleCreate(
            source_key="observe",
            modality="text",
            sha256="c" * 64,
            object_key="private/observe",
            size_bytes=1,
        ),
    )
    operations = OperationService(sessions)
    op = operations.admit_verification(
        actor,
        ns,
        VerificationRequest(
            request_key="observe", sample_id=revision.sample_id, revision_id=revision.id
        ),
    )
    deployment = uuid4()
    provider = LostResponseProvider(deployment)
    dispatcher = WorkflowDispatcher(sessions, provider, deployment)
    asyncio.run(dispatcher.tick(actor, ns, limit=1))
    operations.start(actor, ns, op.id, 1)
    provider.runs[0].state = Completed()

    async def read_run(run_id):
        assert run_id == provider.runs[0].id
        return provider.runs[0]

    provider.read_flow_run = read_run
    operations.request_reconcile(actor, ns, op.id, 1)
    result = asyncio.run(dispatcher.tick(actor, ns, limit=100))
    assert result.deferred == 0
    assert operations.get(actor, ns, op.id).status == "failed"
    assert operations.detail(actor, ns, op.id).accepted_digest is None
    assert provider.creates == 1


@pytest.mark.parametrize("cancel_new_attempt", [False, True])
def test_old_unknown_submission_is_reconciled_and_acknowledged_after_retry(
    registry, sessions, cancel_new_attempt
):
    registry_service, actor, ns = registry
    revision = registry_service.create_sample(
        actor,
        ns,
        SampleCreate(
            source_key="old-epoch",
            modality="text",
            sha256="d" * 64,
            object_key="private/old-epoch",
            size_bytes=1,
        ),
    )
    operations = OperationService(sessions)
    op = operations.admit_verification(
        actor,
        ns,
        VerificationRequest(
            request_key="old-epoch", sample_id=revision.sample_id, revision_id=revision.id
        ),
    )
    deployment = uuid4()
    provider = LostResponseProvider(deployment)
    dispatcher = WorkflowDispatcher(sessions, provider, deployment)
    assert asyncio.run(dispatcher.tick(actor, ns, limit=1)).deferred == 1
    old = operations.workflow_dispatch(actor, ns, op.id, 1)
    operations.fail(actor, ns, op.id, 1)
    operations.retry(actor, ns, op.id, 1)
    if cancel_new_attempt:
        operations.request_cancel(actor, ns, op.id, 2)
    with sessions.begin() as session:
        session.get(OutboxRow, old.event_id).lease_until = session.scalar(
            select(func.clock_timestamp())
        ) - timedelta(seconds=1)
    result = asyncio.run(dispatcher.tick(actor, ns, limit=1))
    assert result.acknowledged == 1 and result.deferred == 0
    assert provider.lookups == provider.creates == 1
    assert operations.workflow_dispatch(actor, ns, op.id, 1).status == "submitted"
    current = operations.get(actor, ns, op.id)
    assert current.epoch == 2
    assert current.status == ("cancelled" if cancel_new_attempt else "queued")
    with sessions() as session:
        assert session.get(OutboxRow, old.event_id).dispatched


def test_namespace_failure_does_not_stop_other_namespace_delivery(monkeypatch):
    import logging
    from contextlib import asynccontextmanager
    from unittest.mock import Mock

    import verdant.flows
    import verdant.persistence
    from verdant.dispatch import DispatchBatch, serve
    from verdant.domain import Forbidden

    warning = Mock()
    monkeypatch.setattr(logging.getLogger("verdant.dispatch"), "warning", warning)
    first, second = uuid4(), uuid4()
    seen = []
    stop = asyncio.Event()
    monkeypatch.setattr(asyncio, "Event", lambda: stop)
    monkeypatch.setenv("VERDANT_DATABASE_URL", "unused-in-controller-double")
    factory = SimpleNamespace(kw={"bind": Mock()})
    monkeypatch.setattr(verdant.persistence, "make_sessions", lambda _: factory)
    monkeypatch.setattr(verdant.flows, "service_principal", lambda _: object())

    @asynccontextmanager
    async def client():
        yield object()

    monkeypatch.setattr("prefect.client.orchestration.get_client", client)

    async def tick(self, actor, namespace_id, limit=25):
        seen.append(namespace_id)
        if namespace_id == first:
            raise Forbidden("private provider payload must never be logged")
        stop.set()
        return DispatchBatch(0, 0, 0, 0)

    monkeypatch.setattr(WorkflowDispatcher, "tick", tick)
    asyncio.run(serve([first, second], uuid4()))
    assert seen == [first, second]
    warning.assert_called_once_with(
        "workflow delivery failed namespace=%s error=%s", first, "Forbidden"
    )
    factory.kw["bind"].dispose.assert_called_once()


def test_unsupported_work_is_durably_quarantined_without_repeated_claims(registry, sessions):
    from verdant.operations import OperationRequest
    from verdant.persistence import ConsumerInboxRow

    _, actor, ns = registry
    operations = OperationService(sessions)
    op = operations.admit(actor, ns, OperationRequest(request_key="unsupported", kind="other-work"))
    deployment = uuid4()
    provider = LostResponseProvider(deployment)
    dispatcher = WorkflowDispatcher(sessions, provider, deployment)
    first = asyncio.run(dispatcher.tick(actor, ns))
    assert first.quarantined == first.acknowledged == 1
    assert first.deferred == 0 and provider.creates == 0
    assert asyncio.run(dispatcher.tick(actor, ns)).claimed == 0
    assert operations.get(actor, ns, op.id).status == "queued"
    with sessions() as session:
        inbox = session.scalars(
            select(ConsumerInboxRow)
            .join(OperationEventRow, OperationEventRow.id == ConsumerInboxRow.event_id)
            .where(OperationEventRow.operation_id == op.id)
        ).one()
        assert inbox.outcome == "quarantined" and "Unsupported" in inbox.error
