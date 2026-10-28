"""Native Prefect deployment admission with durable identity and lookup-before-reissue."""

import asyncio
from uuid import UUID

from prefect.client.schemas.filters import FlowRunFilter, FlowRunFilterIdempotencyKey

from verdant.domain import Conflict, Principal
from verdant.operations import OperationService, WorkflowReconciliation


class WorkflowProviderUnavailable(Exception):
    """Safe provider error; transport response bodies and credentials are omitted."""


class PrefectSubmission:
    def __init__(self, sessions, client, deployment_id: UUID):
        self.operations = OperationService(sessions)
        self.client = client
        self.deployment_id = deployment_id

    async def submit(self, actor: Principal, namespace_id: UUID, operation_id: UUID, epoch: int):
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
                or run.parameters != parameters
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
