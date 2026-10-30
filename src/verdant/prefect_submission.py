"""Native Prefect deployment admission with durable identity and lookup-before-reissue."""

import asyncio
from uuid import UUID

from prefect.client.schemas.filters import FlowRunFilter, FlowRunFilterIdempotencyKey

from verdant.contracts import SAMPLE_INGESTION_KINDS
from verdant.domain import Conflict, Principal
from verdant.operations import OperationService, WorkflowReconciliation


def parameters_match(actual, expected):
    return isinstance(actual, dict) and type(actual.get("epoch")) is int and actual == expected


class WorkflowProviderUnavailable(Exception):
    """Safe provider error; transport response bodies and credentials are omitted."""


class PrefectSubmission:
    def __init__(self, sessions, client, deployment_id: UUID):
        self.operations = OperationService(sessions)
        self.client = client
        self.deployment_id = deployment_id

    async def submit(
        self,
        actor: Principal,
        namespace_id: UUID,
        operation_id: UUID,
        epoch: int,
        *,
        reconcile_only: bool = False,
    ):
        operations = self.operations
        dispatch = await asyncio.to_thread(
            operations.workflow_dispatch, actor, namespace_id, operation_id, epoch
        )
        if dispatch.deployment_id not in {None, self.deployment_id}:
            raise Conflict("Workflow deployment differs from the persisted dispatch")
        if dispatch.status == "submitted":
            return dispatch
        parameters = {
            "namespace_id": str(namespace_id),
            "operation_id": str(operation_id),
            "epoch": epoch,
        }

        def validate(run):
            if (
                run.deployment_id != self.deployment_id
                or not parameters_match(run.parameters, parameters)
                or run.idempotency_key != dispatch.invocation_key
            ):
                raise Conflict("Provider workflow identity does not match the persisted dispatch")

        if dispatch.status == "unknown":
            try:
                matches = await self.client.read_flow_runs(
                    flow_run_filter=FlowRunFilter(
                        idempotency_key=FlowRunFilterIdempotencyKey(any_=[dispatch.invocation_key])
                    ),
                    limit=2,
                )
            except Exception:
                raise WorkflowProviderUnavailable(
                    "Workflow lookup unavailable; dispatch remains uncertain"
                ) from None
            if len(matches) > 1:
                raise Conflict("Provider returned multiple runs for one invocation key")
            if matches:
                validate(matches[0])
                return await asyncio.to_thread(
                    operations.reconcile_workflow_submission,
                    actor,
                    namespace_id,
                    operation_id,
                    epoch,
                    WorkflowReconciliation(
                        request_digest=dispatch.request_digest,
                        provider_run_id=matches[0].id,
                        evidence="Prefect lookup matched invocation key, deployment and parameters",
                    ),
                )
            dispatch = await asyncio.to_thread(
                operations.reconcile_workflow_submission,
                actor,
                namespace_id,
                operation_id,
                epoch,
                WorkflowReconciliation(
                    request_digest=dispatch.request_digest,
                    evidence="Prefect exact invocation-key lookup returned no run",
                ),
            )
        if reconcile_only:
            return dispatch
        dispatch = await asyncio.to_thread(
            operations.begin_workflow_submission,
            actor,
            namespace_id,
            operation_id,
            epoch,
            self.deployment_id,
        )
        if dispatch.status == "submitted":
            return dispatch
        try:
            run = await self.client.create_flow_run_from_deployment(
                self.deployment_id, parameters=parameters, idempotency_key=dispatch.invocation_key
            )
        except Exception:
            raise WorkflowProviderUnavailable(
                "Workflow submission unavailable; reconcile before retry"
            ) from None
        validate(run)
        return await asyncio.to_thread(
            operations.complete_workflow_submission,
            actor,
            namespace_id,
            operation_id,
            epoch,
            run.id,
        )

    async def reconcile_run(
        self, actor: Principal, namespace_id: UUID, operation_id: UUID, epoch: int
    ):
        dispatch = await self.submit(actor, namespace_id, operation_id, epoch, reconcile_only=True)
        if dispatch.status != "submitted":
            return dispatch
        try:
            run = await self.client.read_flow_run(UUID(dispatch.provider_reference))
        except Exception:
            raise WorkflowProviderUnavailable("Workflow observation unavailable") from None
        if (
            run.id != UUID(dispatch.provider_reference)
            or run.deployment_id != dispatch.deployment_id
            or run.idempotency_key != dispatch.invocation_key
            or not parameters_match(
                run.parameters,
                {
                    "namespace_id": str(namespace_id),
                    "operation_id": str(operation_id),
                    "epoch": epoch,
                },
            )
        ):
            raise Conflict("Provider workflow identity does not match persisted dispatch")
        state = await asyncio.to_thread(self.operations.get, actor, namespace_id, operation_id)
        if state.epoch != epoch:
            raise Conflict("Operation attempt is stale")
        if run.state is not None and run.state.is_final():
            if state.status == "cancellation_requested":
                if state.kind in SAMPLE_INGESTION_KINDS:
                    from verdant.ingestion import IngestionService

                    await asyncio.to_thread(
                        IngestionService(
                            self.operations.sessions, bucket="unconfigured"
                        ).ensure_cleanup,
                        actor,
                        namespace_id,
                        operation_id,
                        epoch,
                    )
                elif state.kind == "ingest_cleanup":
                    await asyncio.to_thread(
                        self.operations.confirm_cancel, actor, namespace_id, operation_id, epoch
                    )
                else:
                    await asyncio.to_thread(
                        self.operations.cancel_verification,
                        actor,
                        namespace_id,
                        operation_id,
                        epoch,
                    )
            elif state.status in {"queued", "running"}:
                # Provider completion cannot establish a logical result. Missing acceptance fails
                # safely, allowing an explicit retry to reconcile the read-only receipt.
                await asyncio.to_thread(
                    self.operations.fail, actor, namespace_id, operation_id, epoch
                )
        return dispatch
