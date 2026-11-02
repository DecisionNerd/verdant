import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from verdant.contracts import DatasetCreate, MembershipWrite
from verdant.domain import Conflict, Forbidden
from verdant.ingestion_runner import IngestionRunner
from verdant.model_profiles import ModelAdapter, ModelProfileService, ProfileCreate
from verdant.operations import OperationService
from verdant.persistence import EffectReceiptRow, ProcessOutputRow
from verdant.process_flow import FlowCreate, FlowStep, ProcessAction, ProcessService, ProcessStart
from verdant.process_runner import ProcessRunner
from verdant.versioning import CandidateCapture, CandidatePrepare, VersioningService


@pytest.fixture
def process_fixture(ready_ingestion, registry, sessions):
    create, actor, ns, storage = ready_ingestion
    registry_service, _, _ = registry
    ingestion, batch = create(
        entries=[("one", b"  SAFE One  "), ("two", b"SAFE Two")],
        metadata={"truth_label": "HELD-OUT-SECRET", "answer": {"label": "secret"}},
    )
    IngestionRunner(sessions, storage).run(actor, ns, batch.operation.id, 1)
    batch = ingestion.get(actor, ns, batch.operation.id)
    dataset = registry_service.create_dataset(
        actor, ns, DatasetCreate(name="Process", slug="process", modality="text")
    )
    for revision, item in enumerate(batch.items):
        registry_service.set_membership(
            actor,
            ns,
            dataset.id,
            item.sample_id,
            MembershipWrite(
                expected_revision=revision,
                sample_revision_id=item.accepted_revision_id,
                split="test",
            ),
        )
    versions = VersioningService(sessions)
    candidate = versions.capture(
        actor, ns, dataset.id, CandidateCapture(request_key=str(uuid4()), expected_revision=2)
    )
    versions.prepare(
        actor, ns, dataset.id, candidate.id, CandidatePrepare(expected_count=0, limit=25)
    )
    return actor, ns, dataset, candidate, storage


def make_flow(sessions, fixture, profile_id=None):
    actor, ns, dataset, candidate, storage = fixture
    steps = [FlowStep(name="Trim", kind="preprocess", transform="strip")]
    if profile_id:
        steps.append(
            FlowStep(name="Model", kind="model", profile_id=profile_id, prompt="Classify content")
        )
    else:
        steps.append(FlowStep(name="Lowercase", kind="preprocess", transform="lowercase"))
    service = ProcessService(sessions)
    flow = service.create_flow(actor, ns, dataset.id, FlowCreate(name="Pipeline", steps=steps))
    command = ProcessStart(request_key=str(uuid4()), flow_id=flow.id, candidate_id=candidate.id)
    operation = service.start(actor, ns, dataset.id, command)
    return service, flow, command, operation


def test_actual_datachain_two_step_run_pins_outputs_and_idempotent_admission(
    process_fixture, sessions, principal_factory
):
    actor, ns, dataset, candidate, storage = process_fixture
    service, flow, command, operation = make_flow(sessions, process_fixture)
    assert service.start(actor, ns, dataset.id, command).id == operation.id
    runner = ProcessRunner(sessions, storage)
    assert runner.run(actor, ns, operation.id, 1, limit=1).status == "running"
    assert runner.run(actor, ns, operation.id, 1, limit=1).status == "succeeded"
    detail = service.detail(actor, ns, operation.id)
    assert detail["input"]["generation_id"] == candidate.generation_id
    assert len(detail["outputs"]) == 4
    assert sorted(o["result"]["text"] for o in detail["outputs"] if o["step_position"] == 1) == [
        "safe one",
        "safe two",
    ]
    assert all(
        o["receipt_id"] and o["input_digest"] and o["output_digest"] for o in detail["outputs"]
    )
    assert runner.run(actor, ns, operation.id, 1).status == "succeeded"
    with pytest.raises(Forbidden):
        service.detail(principal_factory(), ns, operation.id)
    with sessions.begin() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text("UPDATE process_flows SET name='mutated' WHERE id=:id"), {"id": flow.id}
            )


def test_pause_resume_cancel_fence_epochs_and_owning_actions(process_fixture, sessions):
    actor, ns, dataset, candidate, storage = process_fixture
    service, flow, command, operation = make_flow(sessions, process_fixture)
    assert (
        service.action(actor, ns, operation.id, ProcessAction(epoch=1, action="pause")).status
        == "paused"
    )
    assert ProcessRunner(sessions, storage).run(actor, ns, operation.id, 1).status == "paused"
    resumed = service.action(actor, ns, operation.id, ProcessAction(epoch=1, action="resume"))
    assert resumed.epoch == 2 and resumed.status == "queued"
    with pytest.raises(Conflict):
        ProcessRunner(sessions, storage).run(actor, ns, operation.id, 1)
    with pytest.raises(Conflict):
        OperationService(sessions).request_cancel(actor, ns, operation.id, 2)
    assert (
        service.action(actor, ns, operation.id, ProcessAction(epoch=2, action="cancel")).status
        == "cancelled"
    )
    assert ProcessRunner(sessions, storage).run(actor, ns, operation.id, 2).status == "cancelled"
    fresh = service.start(
        actor,
        ns,
        dataset.id,
        command.model_copy(update={"request_key": str(uuid4()), "trial_of": operation.id}),
    )
    assert fresh.id != operation.id
    assert ProcessRunner(sessions, storage).run(actor, ns, fresh.id, 1).status == "succeeded"


def model_profile(sessions, actor, ns):
    return ModelProfileService(sessions).create(
        actor,
        ns,
        ProfileCreate(
            name="Fixture",
            provider="glm",
            model="glm-4",
            identity_kind="mutable_alias",
            credential_ref="VERDANT_MODEL_SECRET_FIXTURE",
        ),
    )


def test_model_call_receipts_switch_and_heldout_metadata_never_enter_calls(
    process_fixture, sessions, monkeypatch
):
    actor, ns, dataset, candidate, storage = process_fixture
    profile = model_profile(sessions, actor, ns)
    service, flow, command, operation = make_flow(sessions, process_fixture, profile.id)
    seen = []
    monkeypatch.setenv("VERDANT_MODEL_SECRET_FIXTURE", "fixture-only")

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        assert body["messages"][1]["content"].strip().startswith("SAFE")
        assert "HELD-OUT-SECRET" not in request.content.decode()
        assert "truth" not in request.content.decode()
        return httpx.Response(
            200,
            json={
                "id": str(uuid4()),
                "model": "glm-reported",
                "choices": [{"finish_reason": "stop", "message": {"content": "classified"}}],
            },
        )

    runner = ProcessRunner(
        sessions, storage, adapter=ModelAdapter(transport=httpx.MockTransport(handler))
    )
    assert runner.run(actor, ns, operation.id, 1).status == "succeeded"
    detail = service.detail(actor, ns, operation.id)
    model_outputs = [o for o in detail["outputs"] if o["step_position"] == 1]
    assert len(seen) == len(model_outputs) == 2
    assert all(
        o["profile_digest"] == profile.config_digest and o["result"]["input_tokens"] is None
        for o in model_outputs
    )
    assert runner.run(actor, ns, operation.id, 1).status == "succeeded" and len(seen) == 2


def test_accepted_timeout_stays_unknown_and_no_recovery_replays(
    process_fixture, sessions, monkeypatch
):
    actor, ns, dataset, candidate, storage = process_fixture
    profile = model_profile(sessions, actor, ns)
    service, flow, command, operation = make_flow(sessions, process_fixture, profile.id)
    monkeypatch.setenv("VERDANT_MODEL_SECRET_FIXTURE", "fixture-only")
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("accepted but response lost")

    runner = ProcessRunner(
        sessions, storage, adapter=ModelAdapter(transport=httpx.MockTransport(handler))
    )
    assert runner.run(actor, ns, operation.id, 1).status == "reconciliation_needed"
    assert service.detail(actor, ns, operation.id)["needs_reconciliation"]
    with pytest.raises(Conflict):
        service.action(actor, ns, operation.id, ProcessAction(epoch=1, action="retry"))
    assert runner.run(actor, ns, operation.id, 1).status == "reconciliation_needed"
    assert len(calls) == 1
    assert (
        service.action(actor, ns, operation.id, ProcessAction(epoch=1, action="cancel")).status
        == "cancellation_requested"
    )
    assert runner.run(actor, ns, operation.id, 1).status == "cancellation_requested"


def test_confirmed_rejection_recovers_same_receipts_and_new_epoch(
    process_fixture, sessions, monkeypatch
):
    actor, ns, dataset, candidate, storage = process_fixture
    profile = model_profile(sessions, actor, ns)
    service, flow, command, operation = make_flow(sessions, process_fixture, profile.id)
    monkeypatch.setenv("VERDANT_MODEL_SECRET_FIXTURE", "fixture-only")
    rejected = ModelAdapter(
        transport=httpx.MockTransport(lambda r: httpx.Response(429)), sleep=lambda _: None
    )
    assert (
        ProcessRunner(sessions, storage, adapter=rejected).run(actor, ns, operation.id, 1).status
        == "failed"
    )
    with sessions() as session:
        receipts = session.scalars(
            select(EffectReceiptRow).where(EffectReceiptRow.operation_id == operation.id)
        ).all()
        before = {r.id: r.status for r in receipts}
    assert "unknown" not in before.values()
    recovered = service.action(actor, ns, operation.id, ProcessAction(epoch=1, action="retry"))
    assert recovered.epoch == 2
    good = ModelAdapter(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"choices": [{"finish_reason": "stop", "message": {"content": "done"}}]}
            )
        )
    )
    assert (
        ProcessRunner(sessions, storage, adapter=good).run(actor, ns, operation.id, 2).status
        == "succeeded"
    )
    with sessions() as session:
        ids = set(
            session.scalars(
                select(EffectReceiptRow.id).where(EffectReceiptRow.operation_id == operation.id)
            )
        )
    assert set(before).issubset(ids)
    detail = service.detail(actor, ns, operation.id)
    assert {o["accepted_epoch"] for o in detail["outputs"]} == {1, 2}
    assert len(detail["attempts"]) == 2


@pytest.mark.parametrize("intent,status", [("pause", "paused"), ("cancel", "cancelled")])
def test_inflight_confirmed_rejection_preserves_user_intent(
    process_fixture, sessions, monkeypatch, intent, status
):
    actor, ns, dataset, candidate, storage = process_fixture
    profile = model_profile(sessions, actor, ns)
    service, flow, command, operation = make_flow(sessions, process_fixture, profile.id)
    monkeypatch.setenv("VERDANT_MODEL_SECRET_FIXTURE", "fixture-only")

    def handler(request):
        pending = service.action(actor, ns, operation.id, ProcessAction(epoch=1, action=intent))
        assert pending.status == (
            "pause_requested" if intent == "pause" else "cancellation_requested"
        )
        return httpx.Response(400)

    runner = ProcessRunner(
        sessions, storage, adapter=ModelAdapter(transport=httpx.MockTransport(handler))
    )
    assert runner.run(actor, ns, operation.id, 1).status == status
    assert not service.detail(actor, ns, operation.id)["needs_reconciliation"]


def test_native_final_reconciles_pure_transform_crash_and_preserves_model_unknown(
    process_fixture, sessions
):
    from verdant.model_profiles import config_digest
    from verdant.operations import EffectRequest

    actor, ns, dataset, candidate, storage = process_fixture
    service, flow, command, operation = make_flow(sessions, process_fixture)
    service.worker_state(actor, ns, operation.id, 1)
    definition, items = service.plan_page(actor, ns, operation.id, 1, storage, limit=1)
    item, step = items[0], definition["steps"][0]
    receipt = service.operations.prepare_effect(
        actor,
        ns,
        operation.id,
        1,
        EffectRequest(
            kind="process.preprocess",
            item=f"{item['sample_id']}:0",
            input_revision=str(item["revision_id"]),
            request={"input_digest": item["sha256"], "step_digest": config_digest(step)},
        ),
    )
    service.operations.begin_effect(actor, ns, operation.id, 1, receipt.id)
    # Simulate a stopped native worker after durable dispatch, before local acceptance.
    state = service.reconcile_completed_execution(actor, ns, operation.id, 1)
    assert state.status == "failed"
    assert not service.detail(actor, ns, operation.id)["needs_reconciliation"]
    service.action(actor, ns, operation.id, ProcessAction(epoch=1, action="retry"))
    assert ProcessRunner(sessions, storage).run(actor, ns, operation.id, 2).status == "succeeded"


def test_acceptance_rejects_forged_member_step_and_digest(process_fixture, sessions):
    from verdant.model_profiles import ModelResult, config_digest
    from verdant.operations import EffectRequest

    actor, ns, dataset, candidate, storage = process_fixture
    service, flow, command, operation = make_flow(sessions, process_fixture)
    service.worker_state(actor, ns, operation.id, 1)
    definition, items = service.plan_page(actor, ns, operation.id, 1, storage, limit=1)
    item, step = items[0], definition["steps"][0]
    receipt = service.operations.prepare_effect(
        actor,
        ns,
        operation.id,
        1,
        EffectRequest(
            kind="process.preprocess",
            item=f"{item['sample_id']}:0",
            input_revision=str(item["revision_id"]),
            request={"input_digest": item["sha256"], "step_digest": config_digest(step)},
        ),
    )
    receipt = service.operations.begin_effect(actor, ns, operation.id, 1, receipt.id)
    for forged_item, position, forged_step, digest in [
        (item | {"revision_id": uuid4()}, 0, step, item["sha256"]),
        (item, 1, step, item["sha256"]),
        (item, 0, step | {"transform": "identity"}, item["sha256"]),
        (item, 0, step, "0" * 64),
    ]:
        with pytest.raises(Conflict):
            service.accept(
                actor,
                ns,
                operation.id,
                1,
                forged_item,
                position,
                forged_step,
                receipt,
                digest,
                ModelResult(text="forged"),
            )
    with sessions() as session:
        assert session.get(ProcessOutputRow, (operation.id, item["sample_id"], 0)) is None
        assert session.get(EffectReceiptRow, receipt.id).status == "unknown"
