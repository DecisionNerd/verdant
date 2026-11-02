"""Fixed, bounded DataChain execution over exact sample bytes; no user Python surface."""

import hashlib
import io
import os
import time

from sqlalchemy.exc import SQLAlchemyError

from verdant.domain import Conflict
from verdant.model_inputs import ModelInput, project_model_input
from verdant.model_profiles import ModelAdapter, ModelCallError, ModelResult, config_digest
from verdant.operations import EffectReconciliation, EffectRequest
from verdant.process_flow import ProcessService
from verdant.storage import ObjectDigest, StorageError


class ProcessRunner:
    def __init__(self, sessions, storage, *, adapter=None):
        self.service = ProcessService(sessions)
        self.operations = self.service.operations
        self.storage = storage
        self.adapter = adapter or ModelAdapter()

    def run(self, actor, ns, operation_id, epoch, *, limit=25, deadline=None):
        state = self.service.worker_state(actor, ns, operation_id, epoch)
        if state.status != "running":
            return state
        try:
            definition, items = self.service.plan_page(
                actor, ns, operation_id, epoch, self.storage, limit
            )
            if not items:
                return self.service.finish(actor, ns, operation_id, epoch)
        except (Conflict, StorageError):
            return self.service.fail(actor, ns, operation_id, epoch, "input_unavailable")
        # The catalog contains only ephemeral sample UUIDs/booleans, never content, prompts,
        # credentials, truth or manifests. Each page session drops its temporary datasets.
        os.environ.setdefault("DATACHAIN_NO_ANALYTICS", "1")
        import datachain as dc

        catalog = dc.Session.get(in_memory=True).catalog
        by_id = {str(item["sample_id"]): item for item in items}

        def execute(sample_key: str) -> bool:
            item = by_id[sample_key]
            try:
                state = self.service.worker_state(actor, ns, operation_id, epoch)
                if state.status != "running":
                    return False
                if deadline is not None and time.monotonic() >= deadline:
                    self.service.fail(actor, ns, operation_id, epoch, "worker_time_budget")
                    return False
                source = io.BytesIO()
                self.storage.verify(
                    item["object_key"],
                    ObjectDigest(item["sha256"], item["size_bytes"]),
                    destination=source,
                )
                value = project_model_input(source.getvalue())
                for position, step in enumerate(definition["steps"]):
                    if (
                        self.service.worker_state(actor, ns, operation_id, epoch).status
                        != "running"
                    ):
                        return False
                    if deadline is not None and time.monotonic() >= deadline:
                        self.service.fail(actor, ns, operation_id, epoch, "worker_time_budget")
                        return False
                    accepted = self.service.output(
                        actor, ns, operation_id, item["sample_id"], position
                    )
                    if accepted is not None:
                        value = ModelInput(text=accepted["text"])
                        continue
                    input_digest = hashlib.sha256(value.text.encode()).hexdigest()
                    receipt = self.operations.prepare_effect(
                        actor,
                        ns,
                        operation_id,
                        epoch,
                        EffectRequest(
                            kind="process." + step["kind"],
                            item=f"{item['sample_id']}:{position}",
                            input_revision=str(item["revision_id"]),
                            request={
                                "input_digest": input_digest,
                                "step_digest": config_digest(step),
                            },
                        ),
                    )
                    if receipt.status == "unknown":
                        if step["kind"] == "model":
                            self.service.fail(
                                actor,
                                ns,
                                operation_id,
                                epoch,
                                "model_outcome_unknown",
                                uncertain=True,
                            )
                            return False
                        receipt = self.operations.reconcile_effect(
                            actor,
                            ns,
                            operation_id,
                            receipt.id,
                            EffectReconciliation(
                                expected_revision=receipt.revision,
                                outcome="absent",
                                evidence="Pure transform has no external effects; recompute safely",
                            ),
                        )
                    elif receipt.status == "succeeded":
                        raise Conflict("Accepted receipt is missing its atomic output")
                    profile = credential = None
                    if step["kind"] == "model":
                        profile, credential = self.service.profile_for_step(actor, ns, step)
                        if (
                            self.adapter.transport is None
                            and os.environ.get("VERDANT_MODEL_CALLS_ENABLED") != "1"
                        ):
                            self.service.fail(
                                actor, ns, operation_id, epoch, "provider_calls_disabled"
                            )
                            return False
                    receipt = self.operations.begin_effect(
                        actor, ns, operation_id, epoch, receipt.id
                    )
                    try:
                        if step["kind"] == "preprocess":
                            text = value.text
                            if step["transform"] == "strip":
                                text = text.strip()
                            elif step["transform"] == "lowercase":
                                text = text.lower()
                            result = ModelResult(text=text)
                        else:
                            result = self.adapter.generate(
                                profile,
                                credential,
                                value,
                                prompt=step["prompt"],
                                output_contract=step["output_contract"],
                                deadline=deadline,
                            )
                    except ModelCallError as exc:
                        if not exc.uncertain:
                            self.service.reject_effect(actor, ns, operation_id, receipt, exc.code)
                        self.service.fail(
                            actor, ns, operation_id, epoch, exc.code, uncertain=exc.uncertain
                        )
                        return False
                    self.service.accept(
                        actor,
                        ns,
                        operation_id,
                        epoch,
                        item,
                        position,
                        step,
                        receipt,
                        input_digest,
                        result,
                    )
                    value = ModelInput(text=result.text)
                self.service.checkpoint(actor, ns, operation_id, epoch, item)
                return True
            except StorageError:
                self.service.fail(actor, ns, operation_id, epoch, "input_storage_unavailable")
                return False
            except (Conflict, ValueError, SQLAlchemyError):
                state = self.service.worker_state(actor, ns, operation_id, epoch)
                if state.status == "running":
                    # A DB acceptance failure after HTTP success cannot establish absence.
                    self.service.fail(
                        actor, ns, operation_id, epoch, "acceptance_incomplete", uncertain=True
                    )
                return False

        with dc.Session(catalog=catalog) as batch:
            chain = dc.read_values(sample_key=list(by_id), session=batch).settings(
                parallel=False, prefetch=0, cache=False, batch_size=1
            )
            # Actual DataChain executes fixed domain code serially; only a bounded page
            # is materialized. All content is consumed/verified one artifact at a time.
            completed = chain.map(done=execute, output={"done": bool}).to_values("done")
        state = self.service.worker_state(actor, ns, operation_id, epoch)
        if state.status == "running" and all(completed):
            detail = self.service.detail(actor, ns, operation_id, limit=1)
            if detail["input"]["completed_count"] == detail["input"]["member_count"]:
                return self.service.finish(actor, ns, operation_id, epoch)
        return state
