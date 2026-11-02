"""Versioned sequential processing; PostgreSQL owns pins, progress and effect acceptance."""

import hashlib
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import func, select

from verdant.contracts import Contract, Digest
from verdant.domain import Conflict, NotFound
from verdant.identity import current_principal, require_current_access
from verdant.model_profiles import ModelResult, config_digest
from verdant.operations import (
    EffectReconciliation,
    Operation,
    OperationRequest,
    OperationService,
)
from verdant.persistence import (
    ArtifactLocationRow,
    ArtifactRow,
    AuditRow,
    CandidateRow,
    DatasetRow,
    DatasetVersionRow,
    DraftRow,
    EffectReceiptRow,
    GenerationMemberRow,
    ModelProfileRow,
    OperationRow,
    ProcessAttemptRow,
    ProcessFlowRow,
    ProcessOutputRow,
    ProcessRunRow,
    SampleArtifactRow,
    SampleRevisionRow,
    SampleRow,
    WorkflowDispatchRow,
)
from verdant.telemetry import observed


class FlowStep(Contract):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9 _.-]+$")
    kind: Literal["preprocess", "model"]
    transform: Literal["identity", "strip", "lowercase"] = "identity"
    prompt: str = Field(default="", max_length=4096)
    output_contract: Literal["text", "json"] = "text"
    profile_id: UUID | None = None

    @model_validator(mode="after")
    def supported(self):
        if self.kind == "model":
            if self.profile_id is None or not self.prompt or self.transform != "identity":
                raise ValueError(
                    "Model steps require a profile and prompt; preprocessing is separate"
                )
        elif self.profile_id is not None or self.prompt or self.output_contract != "text":
            raise ValueError("Preprocessing accepts only a supported text transform")
        return self


class FlowCreate(Contract):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9 _.-]*$")
    expected_revision: int = Field(default=0, ge=0, strict=True)
    steps: list[FlowStep] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_steps(self):
        if len({step.name for step in self.steps}) != len(self.steps):
            raise ValueError("Step names must be unique")
        return self


class PinnedFlowStep(FlowStep):
    profile_digest: Digest | None = None

    @model_validator(mode="after")
    def pinned_profile(self):
        if (self.kind == "model") != (self.profile_digest is not None):
            raise ValueError("Model steps require an immutable profile digest")
        return self


class FlowDefinition(Contract):
    schema_version: Literal["verdant.process.v1"] = Field(alias="schema")
    steps: list[PinnedFlowStep] = Field(min_length=1, max_length=8)


class Flow(Contract):
    id: UUID
    namespace_id: UUID
    dataset_id: UUID
    name: str
    revision: int
    definition: FlowDefinition
    config_digest: Digest
    created_at: datetime


class FlowPage(Contract):
    items: list[Flow]
    next_after: UUID | None = None


class ProcessRunInput(Contract):
    dataset_id: UUID
    candidate_id: UUID
    version_id: UUID | None
    generation_id: UUID
    member_count: int = Field(ge=1, le=1000)
    completed_count: int = Field(ge=0, le=1000)
    trial_of: UUID | None


class ProcessAttempt(Contract):
    epoch: int = Field(ge=1)
    status: str
    error_code: str | None
    provider_run_id: str | None
    invocation_key: str | None


class ProcessOutput(Contract):
    sample_id: UUID
    sample_revision_id: UUID
    step_position: int = Field(ge=0, le=7)
    receipt_id: UUID
    accepted_epoch: int = Field(ge=1)
    input_digest: Digest
    output_digest: Digest
    profile_id: UUID | None
    profile_digest: Digest | None
    result: ModelResult


class ProcessDetail(Contract):
    operation: Operation
    flow: Flow
    input: ProcessRunInput
    needs_reconciliation: bool
    attempts: list[ProcessAttempt]
    outputs: list[ProcessOutput]
    next_after: UUID | None


class ProcessStart(Contract):
    request_key: str = Field(min_length=1, max_length=200)
    flow_id: UUID
    candidate_id: UUID | None = None
    version_id: UUID | None = None
    max_members: int = Field(default=100, ge=1, le=1000, strict=True)
    max_model_calls: int = Field(default=100, ge=0, le=1000, strict=True)
    trial_of: UUID | None = None

    @model_validator(mode="after")
    def exact_input(self):
        if (self.candidate_id is None) == (self.version_id is None):
            raise ValueError("Pin exactly one sealed candidate or dataset version")
        return self


class ProcessAction(Contract):
    epoch: int = Field(ge=1, strict=True)
    action: Literal["pause", "resume", "cancel", "retry", "reconcile"]


class ProcessService:
    def __init__(self, sessions):
        self.sessions = sessions
        self.operations = OperationService(sessions)

    @staticmethod
    def _flow(row):
        return Flow(**{key: getattr(row, key) for key in Flow.model_fields})

    @staticmethod
    def _audit(session, actor, ns, aggregate, action, payload):
        session.add(
            AuditRow(
                namespace_id=ns,
                actor_id=actor.id,
                aggregate_id=aggregate,
                action=action,
                schema_version=1,
                payload=payload | {"transport": actor.transport},
            )
        )

    @observed("process")
    def create_flow(self, actor, ns, dataset_id, command: FlowCreate):
        command = FlowCreate.model_validate(command.model_dump())
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(ns, write=True)
            dataset = session.scalar(
                select(DatasetRow)
                .where(DatasetRow.namespace_id == ns, DatasetRow.id == dataset_id)
                .with_for_update()
            )
            if dataset is None:
                raise NotFound("Dataset not found")
            require_current_access(session, actor.id, ns, write=True)
            if dataset.availability != "active":
                raise Conflict("Archived datasets pause new flow versions")
            latest = (
                session.scalar(
                    select(func.max(ProcessFlowRow.revision)).where(
                        ProcessFlowRow.namespace_id == ns,
                        ProcessFlowRow.dataset_id == dataset_id,
                        ProcessFlowRow.name == command.name,
                    )
                )
                or 0
            )
            if latest != command.expected_revision:
                raise Conflict("Flow revision changed")
            steps = []
            for step in command.steps:
                pinned = step.model_dump(mode="json")
                pinned["profile_digest"] = None
                if step.profile_id:
                    profile = session.get(ModelProfileRow, step.profile_id)
                    if profile is None or profile.namespace_id != ns:
                        raise NotFound("Model profile not found")
                    if step.output_contract not in profile.definition["capabilities"]:
                        raise Conflict("Profile does not support the step output contract")
                    pinned["profile_digest"] = profile.config_digest
                steps.append(pinned)
            definition = {"schema": "verdant.process.v1", "steps": steps}
            row = ProcessFlowRow(
                namespace_id=ns,
                dataset_id=dataset_id,
                name=command.name,
                revision=latest + 1,
                definition=definition,
                config_digest=config_digest(definition),
                creator_id=actor.id,
            )
            session.add(row)
            session.flush()
            self._audit(
                session,
                actor,
                ns,
                row.id,
                "process_flow.created",
                {"config_digest": row.config_digest, "revision": row.revision},
            )
            return self._flow(row)

    def list_flows(self, actor, ns, dataset_id, after=None, limit=50):
        if not 1 <= limit <= 100:
            raise ValueError("Flow page must be between 1 and 100")
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(ns)
            query = select(ProcessFlowRow).where(
                ProcessFlowRow.namespace_id == ns, ProcessFlowRow.dataset_id == dataset_id
            )
            if after:
                query = query.where(ProcessFlowRow.id > after)
            rows = session.scalars(query.order_by(ProcessFlowRow.id).limit(limit + 1)).all()
            return {
                "items": [self._flow(row) for row in rows[:limit]],
                "next_after": rows[limit - 1].id if len(rows) > limit else None,
            }

    def get_flow(self, actor, ns, dataset_id, flow_id):
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(ns)
            row = session.get(ProcessFlowRow, flow_id)
            if row is None or (row.namespace_id, row.dataset_id) != (ns, dataset_id):
                raise NotFound("Flow not found")
            return self._flow(row)

    @observed("process")
    def start(self, actor, ns, dataset_id, command: ProcessStart):
        command = ProcessStart.model_validate(command.model_dump())
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(ns, write=True)
            # Shared lock serializes new admissions against dataset archival.
            dataset = session.scalar(
                select(DatasetRow)
                .where(DatasetRow.namespace_id == ns, DatasetRow.id == dataset_id)
                .with_for_update()
            )
            if dataset is None:
                raise NotFound("Dataset not found")
            flow = session.get(ProcessFlowRow, command.flow_id)
            if flow is None or (flow.namespace_id, flow.dataset_id) != (ns, dataset_id):
                raise NotFound("Flow not found")
            version = (
                session.get(DatasetVersionRow, command.version_id) if command.version_id else None
            )
            if command.version_id and (
                version is None or (version.namespace_id, version.dataset_id) != (ns, dataset_id)
            ):
                raise NotFound("Version not found")
            candidate_id = version.candidate_id if version else command.candidate_id
            candidate = session.get(CandidateRow, candidate_id)
            draft = session.get(DraftRow, candidate.draft_id) if candidate else None
            if candidate is None or candidate.namespace_id != ns or draft.dataset_id != dataset_id:
                raise NotFound("Candidate not found")
            if (
                candidate.state != "sealed"
                or not 1 <= candidate.prepared_count <= command.max_members
            ):
                raise Conflict("Input must be sealed, nonempty and within the member budget")
            model_steps = sum(step["kind"] == "model" for step in flow.definition["steps"])
            if model_steps * candidate.prepared_count > command.max_model_calls:
                raise Conflict("Pinned input exceeds the model-call budget")
            if command.trial_of:
                prior = session.get(ProcessRunRow, command.trial_of)
                if prior is None or (prior.namespace_id, prior.dataset_id) != (ns, dataset_id):
                    raise NotFound("Original trial not found")
            inputs = command.model_dump(mode="json", exclude={"request_key"}) | {
                "dataset_id": str(dataset_id),
                "generation_id": str(candidate.generation_id),
                "flow_digest": flow.config_digest,
                "member_count": candidate.prepared_count,
            }
            # Admit idempotently before availability checks only for an existing identity.
            # Rollback removes the admission when a new request is forbidden.
            operation = self.operations._admit(
                session,
                actor,
                ns,
                OperationRequest(
                    request_key=command.request_key, kind="process_flow", inputs=inputs
                ),
            )
            existing = session.get(ProcessRunRow, operation.id)
            if existing is not None:
                return operation
            require_current_access(session, actor.id, ns, write=True)
            if dataset.availability != "active" or (version and version.availability == "archived"):
                raise Conflict("Archived input cannot start a new run")
            session.add(
                ProcessRunRow(
                    operation_id=operation.id,
                    namespace_id=ns,
                    dataset_id=dataset_id,
                    flow_id=flow.id,
                    candidate_id=candidate.id,
                    version_id=command.version_id,
                    generation_id=candidate.generation_id,
                    member_count=candidate.prepared_count,
                    trial_of=command.trial_of,
                )
            )
            session.add(
                ProcessAttemptRow(
                    operation_id=operation.id, namespace_id=ns, epoch=1, status="queued"
                )
            )
            return operation

    def _locked(self, session, actor, ns, operation_id, epoch=None):
        operation = self.operations._locked_operation(session, actor, ns, operation_id, epoch)
        run = session.get(ProcessRunRow, operation_id)
        if operation.kind != "process_flow" or run is None or run.namespace_id != ns:
            raise NotFound("Process run not found")
        return operation, run

    @staticmethod
    def _unknown(session, operation_id):
        return (
            session.scalar(
                select(EffectReceiptRow.id)
                .where(
                    EffectReceiptRow.operation_id == operation_id,
                    EffectReceiptRow.status == "unknown",
                )
                .limit(1)
            )
            is not None
        )

    def _status(self, session, actor, operation, target, error=None):
        operation.status = target
        operation.revision += 1
        self.operations._record_transition(session, actor, operation)
        attempt = session.get(ProcessAttemptRow, (operation.id, operation.epoch))
        attempt.status, attempt.error_code = target, error

    @observed("process")
    def action(self, actor, ns, operation_id, command: ProcessAction):
        command = ProcessAction.model_validate(command.model_dump())
        if command.action == "reconcile":
            return self.operations.request_reconcile(actor, ns, operation_id, command.epoch)
        with self.sessions.begin() as session:
            operation, run = self._locked(session, actor, ns, operation_id, command.epoch)
            status, action = operation.status, command.action
            unknown = self._unknown(session, operation_id)
            if action in {"resume", "retry"}:
                expected = {"paused"} if action == "resume" else {"failed", "reconciliation_needed"}
                if status not in expected or unknown:
                    raise Conflict("Recovery requires a paused/failed run and reconciled effects")
                dispatch = session.get(WorkflowDispatchRow, (operation_id, operation.epoch))
                if dispatch is not None and dispatch.status == "unknown":
                    raise Conflict("Workflow admission must be reconciled before recovery")
                if operation.epoch == 9223372036854775807:
                    raise Conflict("Operation epoch limit reached")
                operation.epoch += 1
                session.add(
                    ProcessAttemptRow(
                        operation_id=operation_id,
                        namespace_id=ns,
                        epoch=operation.epoch,
                        status="queued",
                    )
                )
                session.flush()
                self._status(session, actor, operation, "queued")
            elif action == "pause":
                if status in {"paused", "pause_requested"}:
                    return self.operations._view(operation)
                if status not in {"queued", "running"}:
                    raise Conflict("Run cannot pause in its current state")
                self._status(session, actor, operation, "pause_requested" if unknown else "paused")
            elif action == "cancel":
                if status == "cancelled":
                    return self.operations._view(operation)
                if status not in {
                    "queued",
                    "running",
                    "paused",
                    "pause_requested",
                    "failed",
                    "reconciliation_needed",
                    "cancellation_requested",
                }:
                    raise Conflict("Run cannot cancel in its current state")
                dispatch = session.get(WorkflowDispatchRow, (operation_id, operation.epoch))
                pending = unknown or (dispatch is not None and dispatch.status == "unknown")
                self._status(
                    session, actor, operation, "cancellation_requested" if pending else "cancelled"
                )
            return self.operations._view(operation)

    def worker_state(self, actor, ns, operation_id, epoch):
        with self.sessions.begin() as session:
            operation, run = self._locked(session, actor, ns, operation_id, epoch)
            if operation.status == "queued":
                self._status(session, actor, operation, "running")
            if operation.status in {
                "pause_requested",
                "cancellation_requested",
            } and not self._unknown(session, operation_id):
                dispatch = session.get(WorkflowDispatchRow, (operation_id, epoch))
                if dispatch is None or dispatch.status != "unknown":
                    self._status(
                        session,
                        actor,
                        operation,
                        "paused" if operation.status == "pause_requested" else "cancelled",
                    )
            return self.operations._view(operation)

    def fail(self, actor, ns, operation_id, epoch, code, *, uncertain=False):
        with self.sessions.begin() as session:
            operation, run = self._locked(session, actor, ns, operation_id, epoch)
            if operation.status in {"succeeded", "paused", "cancelled"}:
                return self.operations._view(operation)
            target = "reconciliation_needed" if uncertain else "failed"
            if operation.status in {"pause_requested", "cancellation_requested"}:
                target = operation.status
                if not self._unknown(session, operation_id):
                    dispatch = session.get(WorkflowDispatchRow, (operation_id, epoch))
                    if dispatch is None or dispatch.status != "unknown":
                        target = "paused" if operation.status == "pause_requested" else "cancelled"
            self._status(session, actor, operation, target, code)
            return self.operations._view(operation)

    def plan_page(self, actor, ns, operation_id, epoch, storage, limit=25):
        if not 1 <= limit <= 25:
            raise ValueError("Process batch must be between 1 and 25")
        with self.sessions.begin() as session:
            operation, run = self._locked(session, actor, ns, operation_id, epoch)
            if operation.status != "running":
                raise Conflict("Process is not running")
            flow = session.get(ProcessFlowRow, run.flow_id)
            query = (
                select(
                    GenerationMemberRow.sample_id,
                    SampleRevisionRow.id,
                    SampleRevisionRow.sha256,
                    SampleRevisionRow.size_bytes,
                    ArtifactRow,
                    SampleRow.modality,
                )
                .join(
                    SampleRevisionRow,
                    SampleRevisionRow.id == GenerationMemberRow.sample_revision_id,
                )
                .join(SampleRow, SampleRow.id == GenerationMemberRow.sample_id)
                .outerjoin(SampleArtifactRow, SampleArtifactRow.revision_id == SampleRevisionRow.id)
                .outerjoin(ArtifactRow, ArtifactRow.id == SampleArtifactRow.artifact_id)
                .where(
                    GenerationMemberRow.namespace_id == ns,
                    GenerationMemberRow.generation_id == run.generation_id,
                )
            )
            if run.cursor:
                query = query.where(GenerationMemberRow.sample_id > run.cursor)
            records = session.execute(
                query.order_by(GenerationMemberRow.sample_id).limit(limit)
            ).all()
            items = []
            for sample_id, revision_id, digest, size, artifact, modality in records:
                if (
                    artifact is None
                    or artifact.namespace_id != ns
                    or artifact.verified_at is None
                    or artifact.collection_state != "active"
                    or (artifact.sha256, artifact.size_bytes) != (digest, size)
                ):
                    raise Conflict("Process input requires a verified registered artifact")
                if modality != "text" or size > 65536:
                    raise Conflict("Process input must be text no larger than 64 KiB")
                location = session.scalar(
                    select(ArtifactLocationRow)
                    .where(
                        ArtifactLocationRow.namespace_id == ns,
                        ArtifactLocationRow.artifact_id == artifact.id,
                        ArtifactLocationRow.store_id == "primary",
                        ArtifactLocationRow.bucket == storage.settings.bucket,
                    )
                    .order_by(ArtifactLocationRow.id)
                    .limit(1)
                )
                if location is None:
                    raise Conflict("Process artifact has no authorized storage placement")
                items.append(
                    {
                        "sample_id": sample_id,
                        "revision_id": revision_id,
                        "sha256": digest,
                        "size_bytes": size,
                        "object_key": location.object_key,
                    }
                )
            return flow.definition, items

    def profile_for_step(self, actor, ns, step):
        from verdant.model_profiles import ModelProfileService

        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(ns, write=True)
            row = session.get(ModelProfileRow, UUID(step["profile_id"]))
            if row is None or row.namespace_id != ns or row.config_digest != step["profile_digest"]:
                raise Conflict("Pinned model profile does not match")
            return ModelProfileService._view(row), row.credential_ref

    def output(self, actor, ns, operation_id, sample_id, position):
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(ns)
            row = session.get(ProcessOutputRow, (operation_id, sample_id, position))
            if row is None:
                return None
            if row.namespace_id != ns:
                raise NotFound("Process output not found")
            return dict(row.result)

    def accept(
        self,
        actor,
        ns,
        operation_id,
        epoch,
        item,
        position,
        step,
        receipt,
        input_digest,
        result: ModelResult,
    ):
        with self.sessions.begin() as session:
            operation, run = self._locked(session, actor, ns, operation_id, epoch)
            effect = self.operations._locked_effect(session, ns, operation_id, receipt.id)
            flow = session.get(ProcessFlowRow, run.flow_id)
            if type(position) is not int or not 0 <= position < len(flow.definition["steps"]):
                raise Conflict("Output step position is invalid")
            pinned_step = flow.definition["steps"][position]
            member = session.get(GenerationMemberRow, (run.generation_id, item["sample_id"]))
            if (
                member is None
                or member.namespace_id != ns
                or member.sample_revision_id != item["revision_id"]
                or pinned_step != step
            ):
                raise Conflict("Output lineage differs from pinned input or flow")
            if position:
                previous = session.get(
                    ProcessOutputRow, (operation_id, item["sample_id"], position - 1)
                )
                if previous is None:
                    raise Conflict("Previous sequential output is missing")
                expected_input = hashlib.sha256(previous.result["text"].encode()).hexdigest()
            else:
                expected_input = session.get(SampleRevisionRow, member.sample_revision_id).sha256
            expected_identity = {
                "kind": "process." + step["kind"],
                "item": f"{item['sample_id']}:{position}",
                "input_revision": str(item["revision_id"]),
            }
            expected_request = {"input_digest": expected_input, "step_digest": config_digest(step)}
            if (
                effect.identity != expected_identity
                or effect.request != expected_request
                or input_digest != expected_input
            ):
                raise Conflict("Output does not match its exact prepared effect")
            result = ModelResult.model_validate(result.model_dump())
            if (effect.dispatch_id, effect.dispatch_epoch) != (
                receipt.dispatch_id,
                epoch,
            ) or effect.status != "unknown":
                raise Conflict("Process output dispatch is stale")
            if operation.status not in {"running", "pause_requested", "cancellation_requested"}:
                raise Conflict("Process output needs reconciliation")
            if (step["kind"] == "model") != (step["profile_id"] is not None):
                raise Conflict("Invalid pinned step")
            value = result.model_dump(mode="json")
            digest = config_digest(value)
            session.add(
                ProcessOutputRow(
                    operation_id=operation_id,
                    namespace_id=ns,
                    sample_id=item["sample_id"],
                    sample_revision_id=item["revision_id"],
                    step_position=position,
                    receipt_id=receipt.id,
                    accepted_epoch=epoch,
                    input_digest=input_digest,
                    output_digest=digest,
                    result=value,
                    profile_id=UUID(step["profile_id"]) if step["profile_id"] else None,
                    profile_digest=step.get("profile_digest"),
                )
            )
            effect.status, effect.result_digest = "succeeded", digest
            effect.provider_reference = (
                result.provider_reference
                or f"process-output:{operation_id}:{item['sample_id']}:{position}"
            )
            effect.revision += 1
            self.operations._effect_event(session, actor, operation, effect, "completed")
            session.flush()

    def reconcile_completed_execution(self, actor, ns, operation_id, epoch):
        """Called only after Prefect proves the exact native invocation is final.

        Pure fixed transforms are safe to recompute. Provider completion says nothing
        about accepted model calls; those receipts remain unknown until provider evidence.
        """
        with self.sessions.begin() as session:
            operation, run = self._locked(session, actor, ns, operation_id, epoch)
            if operation.status in {"succeeded", "cancelled", "paused"}:
                return self.operations._view(operation)
            flow = session.get(ProcessFlowRow, run.flow_id)
            receipts = session.scalars(
                select(EffectReceiptRow)
                .where(
                    EffectReceiptRow.operation_id == operation_id,
                    EffectReceiptRow.status == "unknown",
                )
                .limit(8001)
            ).all()
            if len(receipts) > 8000:
                raise Conflict("Process effect budget exceeded")
            for receipt in receipts:
                if receipt.identity.get("kind") != "process.preprocess":
                    continue
                try:
                    sample, position = receipt.identity["item"].split(":")
                    sample, position = UUID(sample), int(position)
                    step = flow.definition["steps"][position]
                except (KeyError, ValueError, IndexError, TypeError):
                    raise Conflict("Unknown transform receipt has invalid identity") from None
                member = session.get(GenerationMemberRow, (run.generation_id, sample))
                if (
                    position < 0
                    or step["kind"] != "preprocess"
                    or member is None
                    or receipt.identity["input_revision"] != str(member.sample_revision_id)
                    or receipt.request.get("step_digest") != config_digest(step)
                ):
                    raise Conflict("Unknown transform receipt differs from pinned execution")
                self.operations._reconcile_effect(
                    session,
                    actor,
                    ns,
                    operation_id,
                    receipt.id,
                    EffectReconciliation(
                        expected_revision=receipt.revision,
                        outcome="absent",
                        evidence="Native invocation final; pure fixed transform may recompute",
                    ),
                )
            unknown = self._unknown(session, operation_id)
            target = "reconciliation_needed" if unknown else "failed"
            if operation.status in {"pause_requested", "cancellation_requested"}:
                target = (
                    operation.status
                    if unknown
                    else ("paused" if operation.status == "pause_requested" else "cancelled")
                )
            self._status(session, actor, operation, target, "provider_finished_without_acceptance")
            return self.operations._view(operation)

    def reject_effect(self, actor, ns, operation_id, receipt, code):
        # Only a protocol-confirmed rejection may make the SAME receipt dispatchable again.
        return self.operations.reconcile_effect(
            actor,
            ns,
            operation_id,
            receipt.id,
            EffectReconciliation(
                expected_revision=receipt.revision,
                outcome="absent",
                evidence="Provider rejected the bounded call: " + code,
            ),
        )

    def checkpoint(self, actor, ns, operation_id, epoch, item):
        with self.sessions.begin() as session:
            operation, run = self._locked(session, actor, ns, operation_id, epoch)
            flow = session.get(ProcessFlowRow, run.flow_id)
            if operation.status not in {"running", "pause_requested", "cancellation_requested"}:
                return
            if run.cursor is not None and item["sample_id"] <= run.cursor:
                return
            expected_next = session.scalar(
                select(GenerationMemberRow.sample_id)
                .where(
                    GenerationMemberRow.generation_id == run.generation_id,
                    *([GenerationMemberRow.sample_id > run.cursor] if run.cursor else []),
                )
                .order_by(GenerationMemberRow.sample_id)
                .limit(1)
            )
            count = session.scalar(
                select(func.count())
                .select_from(ProcessOutputRow)
                .where(
                    ProcessOutputRow.operation_id == operation_id,
                    ProcessOutputRow.sample_id == item["sample_id"],
                )
            )
            if item["sample_id"] != expected_next or count != len(flow.definition["steps"]):
                raise Conflict("Checkpoint requires every sequential step of the next member")
            run.cursor = item["sample_id"]
            run.completed_count += 1
            self._audit(
                session,
                actor,
                ns,
                operation_id,
                "process.checkpoint",
                {"epoch": epoch, "completed_count": run.completed_count},
            )

    def finish(self, actor, ns, operation_id, epoch):
        with self.sessions.begin() as session:
            operation, run = self._locked(session, actor, ns, operation_id, epoch)
            if operation.status == "succeeded":
                return self.operations._view(operation)
            if (
                operation.status != "running"
                or run.completed_count != run.member_count
                or self._unknown(session, operation_id)
            ):
                raise Conflict("Process cannot finalize without all accepted outputs")
            remaining = session.scalar(
                select(GenerationMemberRow.sample_id)
                .where(
                    GenerationMemberRow.generation_id == run.generation_id,
                    GenerationMemberRow.sample_id > run.cursor,
                )
                .limit(1)
            )
            if remaining is not None:
                raise Conflict("Process input count differs from its sealed generation")
            self._status(session, actor, operation, "succeeded")
            return self.operations._view(operation)

    def list_runs(self, actor, ns, dataset_id, after=None, limit=25):
        if not 1 <= limit <= 100:
            raise ValueError("Run page must be between 1 and100")
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(ns)
            query = (
                select(OperationRow)
                .join(ProcessRunRow, ProcessRunRow.operation_id == OperationRow.id)
                .where(ProcessRunRow.namespace_id == ns, ProcessRunRow.dataset_id == dataset_id)
            )
            if after:
                query = query.where(OperationRow.id > after)
            rows = session.scalars(query.order_by(OperationRow.id).limit(limit + 1)).all()
            return {
                "items": [self.operations._view(row) for row in rows[:limit]],
                "next_after": rows[limit - 1].id if len(rows) > limit else None,
            }

    def detail(self, actor, ns, operation_id, *, after_sample=None, limit=25):
        if not 1 <= limit <= 25:
            raise ValueError("Output page must be between 1 and25 members")
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(ns)
            run = session.get(ProcessRunRow, operation_id)
            if run is None or run.namespace_id != ns:
                raise NotFound("Process run not found")
            operation = session.get(OperationRow, operation_id)
            query = select(ProcessOutputRow.sample_id).where(
                ProcessOutputRow.operation_id == operation_id
            )
            if after_sample:
                query = query.where(ProcessOutputRow.sample_id > after_sample)
            ids = session.scalars(
                query.distinct().order_by(ProcessOutputRow.sample_id).limit(limit + 1)
            ).all()
            outputs = session.scalars(
                select(ProcessOutputRow)
                .where(
                    ProcessOutputRow.operation_id == operation_id,
                    ProcessOutputRow.sample_id.in_(ids[:limit]),
                )
                .order_by(ProcessOutputRow.sample_id, ProcessOutputRow.step_position)
            ).all()
            attempts = session.scalars(
                select(ProcessAttemptRow)
                .where(ProcessAttemptRow.operation_id == operation_id)
                .order_by(ProcessAttemptRow.epoch.desc())
                .limit(100)
            ).all()
            dispatches = {
                row.epoch: row
                for row in session.scalars(
                    select(WorkflowDispatchRow).where(
                        WorkflowDispatchRow.operation_id == operation_id,
                        WorkflowDispatchRow.epoch.in_([attempt.epoch for attempt in attempts]),
                    )
                )
            }
            return {
                "operation": self.operations._view(operation),
                "flow": self._flow(session.get(ProcessFlowRow, run.flow_id)),
                "input": {
                    key: getattr(run, key)
                    for key in (
                        "dataset_id",
                        "candidate_id",
                        "version_id",
                        "generation_id",
                        "member_count",
                        "completed_count",
                        "trial_of",
                    )
                },
                "needs_reconciliation": self._unknown(session, operation_id),
                "attempts": [
                    {
                        "epoch": row.epoch,
                        "status": row.status,
                        "error_code": row.error_code,
                        "provider_run_id": dispatches[row.epoch].provider_reference
                        if row.epoch in dispatches
                        else None,
                        "invocation_key": dispatches[row.epoch].invocation_key
                        if row.epoch in dispatches
                        else None,
                    }
                    for row in attempts
                ],
                "outputs": [
                    {
                        key: getattr(row, key)
                        for key in (
                            "sample_id",
                            "sample_revision_id",
                            "step_position",
                            "receipt_id",
                            "accepted_epoch",
                            "input_digest",
                            "output_digest",
                            "profile_id",
                            "profile_digest",
                            "result",
                        )
                    }
                    for row in outputs
                ],
                "next_after": ids[limit - 1] if len(ids) > limit else None,
            }
