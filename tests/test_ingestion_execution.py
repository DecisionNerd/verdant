"""Real PostgreSQL/S3 execution services; Prefect admission/context are labeled doubles."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

pytest.importorskip("prefect")

from verdant.dispatch import WorkflowDispatcher
from verdant.domain import Conflict
from verdant.ingestion import IngestionService, UploadDeclaration, UploadRequest
from verdant.ingestion_maintenance import IngestionMaintenance
from verdant.ingestion_runner import IngestionRunner
from verdant.operations import EffectReconciliation, OperationService
from verdant.persistence import (
    EffectReceiptRow,
    IngestionCleanupTaskRow,
    OperationEventRow,
    OperationRow,
    OutboxRow,
)


class Provider:
    def __init__(self, *, lose=False):
        self.lose = lose
        self.runs = []
        self.creates = 0
        self.lookups = 0

    async def create_flow_run_from_deployment(self, deployment, *, parameters, idempotency_key):
        self.creates += 1
        run = SimpleNamespace(
            id=uuid4(),
            deployment_id=deployment,
            parameters=parameters,
            idempotency_key=idempotency_key,
            state=SimpleNamespace(is_final=lambda: True),
        )
        self.runs.append(run)
        if self.lose:
            raise TimeoutError("fixture lost response after admission")
        return run

    async def read_flow_runs(self, **kwargs):
        self.lookups += 1
        keys = kwargs["flow_run_filter"].idempotency_key.any_
        return [run for run in self.runs if run.idempotency_key in keys]

    async def read_flow_run(self, identity):
        return next(run for run in self.runs if run.id == identity)


def release_claims(sessions, ns):
    from datetime import timedelta

    with sessions.begin() as session:
        rows = session.scalars(
            select(OutboxRow)
            .join(OperationEventRow, OperationEventRow.id == OutboxRow.event_id)
            .where(OperationEventRow.namespace_id == ns, OutboxRow.claim_id.is_not(None))
        ).all()
        for row in rows:
            row.lease_until = session.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)


def test_cleanup_admission_is_atomic_unique_across_actors_and_immutable(
    ready_ingestion, sessions, principal_factory
):
    create, actor, ns, adapter = ready_ingestion
    service, parent = create()
    cancelled = service.request_cancel(actor, ns, parent.operation.id, 1)
    child_id = cancelled.cleanup_operation_id
    assert child_id is not None and child_id != parent.operation.id
    second = principal_factory(ns)
    assert service.ensure_cleanup(second, ns, parent.operation.id, 1).id == child_id
    with sessions() as session:
        child = session.get(OperationRow, child_id)
        assert child.kind == "ingest_cleanup" and child.status == "queued"
        assert child.inputs == {"parent_operation_id": str(parent.operation.id), "parent_epoch": 1}
    with pytest.raises(DBAPIError, match="immutable"):
        with sessions.begin() as session:
            session.get(OperationRow, child_id).inputs = {
                "parent_operation_id": str(uuid4()),
                "parent_epoch": 1,
            }
    with pytest.raises(DBAPIError, match="Immutable"):
        with sessions.begin() as session:
            session.get(IngestionCleanupTaskRow, child_id).parent_epoch = 2
    completed = IngestionMaintenance(sessions, adapter).run(actor, ns, child_id, 1)
    assert completed.status == "succeeded"
    parent_after = service.get(actor, ns, parent.operation.id)
    assert (
        parent_after.operation.status == "cancelled"
        and parent_after.cleanup_operation_id == child_id
    )


def test_pending_maintenance_retries_without_changing_parent_epoch(
    ready_ingestion, sessions, monkeypatch
):
    create, actor, ns, adapter = ready_ingestion
    service, parent = create()

    def stopped(*args):
        raise RuntimeError("fixture stopped before provider write")

    monkeypatch.setattr(adapter, "promote", stopped)
    with pytest.raises(RuntimeError):
        IngestionRunner(sessions, adapter).run(actor, ns, parent.operation.id, 1)
    cancel = service.request_cancel(actor, ns, parent.operation.id, 1)
    maintenance = IngestionMaintenance(sessions, adapter)
    assert maintenance.run(actor, ns, cancel.cleanup_operation_id, 1).status == "failed"
    with sessions() as session:
        receipt = session.scalar(
            select(EffectReceiptRow).where(
                EffectReceiptRow.operation_id == parent.operation.id,
                EffectReceiptRow.identity["kind"].as_string() == "artifact.promote",
            )
        )
        identity, revision = receipt.id, receipt.revision
    service.operations.reconcile_effect(
        actor,
        ns,
        parent.operation.id,
        identity,
        EffectReconciliation(
            expected_revision=revision,
            outcome="absent",
            evidence="Synchronous fixture never issued provider write",
        ),
    )
    service.operations.retry(actor, ns, cancel.cleanup_operation_id, 1)
    assert maintenance.run(actor, ns, cancel.cleanup_operation_id, 2).status == "succeeded"
    assert service.get(actor, ns, parent.operation.id).operation.epoch == 1
    with pytest.raises(Conflict, match="stale"):
        maintenance.run(actor, ns, cancel.cleanup_operation_id, 1)


def test_maintenance_cancel_and_retry_preserve_parent_request(ready_ingestion, sessions):
    create, actor, ns, adapter = ready_ingestion
    service, parent = create()
    cancel = service.request_cancel(actor, ns, parent.operation.id, 1)
    child = cancel.cleanup_operation_id
    service.operations.request_cancel(actor, ns, child, 1)
    maintenance = IngestionMaintenance(sessions, adapter)
    assert maintenance.run(actor, ns, child, 1).status == "cancelled"
    assert service.get(actor, ns, parent.operation.id).operation.status == "cancellation_requested"
    service.operations.retry(actor, ns, child, 1)
    assert maintenance.run(actor, ns, child, 2).status == "succeeded"


def test_dispatch_routes_ingestion_and_reconciles_lost_admission(ready_ingestion, sessions):
    create, actor, ns, adapter = ready_ingestion
    service, parent = create()
    verification_deployment, ingestion_deployment = uuid4(), uuid4()
    provider = Provider(lose=True)
    dispatcher = WorkflowDispatcher(
        sessions, provider, verification_deployment, ingestion_deployment
    )
    first = asyncio.run(dispatcher.tick(actor, ns, limit=100))
    assert first.deferred == 1 and provider.creates == 1
    assert provider.runs[0].deployment_id == ingestion_deployment
    release_claims(sessions, ns)
    second = asyncio.run(dispatcher.tick(actor, ns, limit=100))
    assert second.deferred == 0 and provider.creates == provider.lookups == 1
    dispatch = service.operations.workflow_dispatch(actor, ns, parent.operation.id, 1)
    assert dispatch.status == "submitted" and dispatch.deployment_id == ingestion_deployment


def test_cancel_before_readiness_admits_only_separate_cleanup_run(registry, sessions):
    _, actor, ns = registry
    service = IngestionService(sessions, bucket="test-private")
    parent = service.admit(
        actor,
        ns,
        UploadRequest(
            request_key="early",
            items=[
                UploadDeclaration(
                    source_key="early",
                    modality="text",
                    sha256="a" * 64,
                    size_bytes=1,
                )
            ],
        ),
    )
    cancel = service.request_cancel(actor, ns, parent.operation.id, 1)
    provider = Provider()
    ingestion_deployment = uuid4()
    dispatcher = WorkflowDispatcher(sessions, provider, uuid4(), ingestion_deployment)
    asyncio.run(dispatcher.tick(actor, ns, limit=100))
    assert provider.creates == 1
    assert provider.runs[0].parameters["operation_id"] == str(cancel.cleanup_operation_id)
    assert provider.runs[0].deployment_id == ingestion_deployment
    assert service.get(actor, ns, parent.operation.id).operation.status == "cancellation_requested"


def test_cancel_after_final_original_run_uses_new_invocation(ready_ingestion, sessions):
    create, actor, ns, adapter = ready_ingestion
    service, parent = create()
    provider = Provider()
    dispatcher = WorkflowDispatcher(sessions, provider, uuid4(), uuid4())
    asyncio.run(dispatcher.tick(actor, ns, limit=100))
    service.operations.fail(actor, ns, parent.operation.id, 1)
    cancel = service.request_cancel(actor, ns, parent.operation.id, 1)
    asyncio.run(dispatcher.tick(actor, ns, limit=100))
    assert provider.creates == 2
    assert provider.runs[0].idempotency_key != provider.runs[1].idempotency_key
    assert provider.runs[1].parameters["operation_id"] == str(cancel.cleanup_operation_id)


@pytest.mark.parametrize(
    "change", ["deployment", "namespace", "operation", "epoch", "bool_epoch", "extra"]
)
def test_worker_admission_rejects_wrong_native_context(registry, sessions, monkeypatch, change):
    from verdant import flows

    _, actor, ns = registry
    service = IngestionService(sessions, bucket="test-private")
    parent = service.admit(
        actor,
        ns,
        UploadRequest(
            request_key="gate",
            items=[
                UploadDeclaration(
                    source_key="gate",
                    modality="text",
                    sha256="a" * 64,
                    size_bytes=1,
                )
            ],
        ),
    )
    cancel = service.request_cancel(actor, ns, parent.operation.id, 1)
    child = cancel.cleanup_operation_id
    with sessions() as session:
        event_id = session.scalar(
            select(OperationEventRow.id).where(
                OperationEventRow.operation_id == child,
                OperationEventRow.event_type == "operation.queued",
            )
        )
    service.operations.receive_workflow_event(actor, ns, event_id)
    deployment = uuid4()
    dispatch = service.operations.begin_workflow_submission(actor, ns, child, 1, deployment)
    parameters = {"namespace_id": str(ns), "operation_id": str(child), "epoch": 1}
    if change in {"namespace", "operation"}:
        parameters[change + "_id"] = str(uuid4())
    elif change == "epoch":
        parameters["epoch"] = 2
    elif change == "bool_epoch":
        parameters["epoch"] = True
    elif change == "extra":
        parameters["unexpected"] = "not admitted"
    run = SimpleNamespace(
        id=uuid4(),
        deployment_id=uuid4() if change == "deployment" else deployment,
        idempotency_key=dispatch.invocation_key,
        parameters=parameters,
    )
    monkeypatch.setattr(flows, "service_principal", lambda _: actor)
    monkeypatch.setattr(flows, "get_run_context", lambda: SimpleNamespace(flow_run=run))
    with pytest.raises(Conflict, match="persisted workflow admission"):
        flows.admitted_worker(sessions, ns, child, 1, {"ingest_cleanup"})
    assert service.operations.workflow_dispatch(actor, ns, child, 1).status == "unknown"


def bind_native_context(monkeypatch, sessions, actor, ns, operation_id, epoch, deployment):
    from verdant import flows

    operations = OperationService(sessions)
    with sessions() as session:
        event_id = session.scalar(
            select(OperationEventRow.id).where(
                OperationEventRow.operation_id == operation_id,
                OperationEventRow.event_type == "operation.queued",
                OperationEventRow.payload["epoch"].as_integer() == epoch,
            )
        )
    operations.receive_workflow_event(actor, ns, event_id)
    dispatch = operations.begin_workflow_submission(actor, ns, operation_id, epoch, deployment)
    run = SimpleNamespace(
        id=uuid4(),
        deployment_id=deployment,
        idempotency_key=dispatch.invocation_key,
        parameters={"namespace_id": str(ns), "operation_id": str(operation_id), "epoch": epoch},
    )
    monkeypatch.setattr(flows, "service_principal", lambda _: actor)
    monkeypatch.setattr(flows, "get_run_context", lambda: SimpleNamespace(flow_run=run))
    monkeypatch.setattr(flows, "make_sessions", lambda _: sessions)
    monkeypatch.setenv("VERDANT_DATABASE_URL", "actual-session-factory-injected")
    return flows


def test_ingestion_flow_body_executes_real_storage_and_linked_maintenance(
    ready_ingestion, sessions, monkeypatch
):
    from verdant.storage import S3Storage

    create, actor, ns, adapter = ready_ingestion
    service, parent = create()
    deployment = uuid4()
    flows = bind_native_context(
        monkeypatch, sessions, actor, ns, parent.operation.id, 1, deployment
    )
    monkeypatch.setattr(flows.S3Storage, "from_env", lambda: S3Storage(adapter.settings))
    result = flows.ingest_operations.fn(str(ns), str(parent.operation.id), 1)
    assert result["status"] == "succeeded"
    accepted = service.get(actor, ns, parent.operation.id)
    assert accepted.items[0].state == "accepted"
    child = accepted.cleanup_operation_id
    bind_native_context(monkeypatch, sessions, actor, ns, child, 1, deployment)
    result = flows.ingest_operations.fn(str(ns), str(child), 1)
    assert result["status"] == "succeeded"
    assert service.get(actor, ns, parent.operation.id).items[0] == accepted.items[0]


def test_native_cancel_handoff_does_not_open_storage(ready_ingestion, sessions, monkeypatch):
    create, actor, ns, adapter = ready_ingestion
    service, parent = create()
    flows = bind_native_context(monkeypatch, sessions, actor, ns, parent.operation.id, 1, uuid4())
    service.request_cancel(actor, ns, parent.operation.id, 1)

    def unexpected():
        pytest.fail("Cancelled ingestion opened a storage client")

    monkeypatch.setattr(flows.S3Storage, "from_env", unexpected)
    result = flows.ingest_operations.fn(str(ns), str(parent.operation.id), 1)
    assert result["status"] == "cancellation_requested"
    assert service.get(actor, ns, parent.operation.id).cleanup_operation_id is not None


def test_old_epoch_ingestion_admission_is_lookup_only(ready_ingestion, sessions):
    create, actor, ns, adapter = ready_ingestion
    service, parent = create()
    provider = Provider(lose=True)
    dispatcher = WorkflowDispatcher(sessions, provider, uuid4(), uuid4())
    asyncio.run(dispatcher.tick(actor, ns, limit=100))
    service.operations.fail(actor, ns, parent.operation.id, 1)
    service.operations.retry(actor, ns, parent.operation.id, 1)
    release_claims(sessions, ns)
    # Process the old claim first; the new attempt remains a separate queued invocation.
    asyncio.run(dispatcher.tick(actor, ns, limit=1))
    assert provider.creates == 1 and provider.lookups == 1
    assert (
        service.operations.workflow_dispatch(actor, ns, parent.operation.id, 1).status
        == "submitted"
    )
    assert service.get(actor, ns, parent.operation.id).operation.epoch == 2


def test_unconfigured_ingestion_remains_replayable_after_deployment_is_added(
    ready_ingestion, sessions
):
    create, actor, ns, adapter = ready_ingestion
    service, parent = create()
    provider = Provider()
    verification_deployment, ingestion_deployment = uuid4(), uuid4()
    missing = WorkflowDispatcher(sessions, provider, verification_deployment)
    first = asyncio.run(missing.tick(actor, ns, limit=100))
    assert first.unconfigured > 0 and first.deferred == first.unconfigured
    assert first.quarantined == 0 and provider.creates == 0
    release_claims(sessions, ns)
    configured = WorkflowDispatcher(
        sessions, provider, verification_deployment, ingestion_deployment
    )
    recovered = asyncio.run(configured.tick(actor, ns, limit=100))
    assert recovered.deferred == recovered.quarantined == 0
    assert provider.creates == 1
    assert (
        service.operations.workflow_dispatch(actor, ns, parent.operation.id, 1).status
        == "submitted"
    )
