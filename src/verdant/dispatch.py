"""Bounded outbox delivery to native Prefect; PostgreSQL remains authoritative."""

import asyncio
from dataclasses import dataclass
from uuid import UUID

from verdant.contracts import SAMPLE_INGESTION_KINDS
from verdant.domain import Conflict, NotFound, Principal
from verdant.ingestion import IngestionService
from verdant.operations import OperationService
from verdant.prefect_submission import PrefectSubmission, WorkflowProviderUnavailable


@dataclass(frozen=True)
class DispatchBatch:
    claimed: int
    acknowledged: int
    deferred: int
    quarantined: int
    unconfigured: int = 0


class WorkflowDispatcher:
    def __init__(
        self, sessions, client, deployment_id: UUID, ingestion_deployment_id: UUID | None = None
    ):
        self.operations = OperationService(sessions)
        self.ingestion = IngestionService(sessions, bucket="unconfigured")
        self.submission = PrefectSubmission(sessions, client, deployment_id)
        self.ingestion_submission = (
            PrefectSubmission(sessions, client, ingestion_deployment_id)
            if ingestion_deployment_id is not None
            else None
        )

    def _submission(self, kind):
        if kind == "verify_sample_artifact":
            return self.submission
        if kind in SAMPLE_INGESTION_KINDS | {"ingest_cleanup"}:
            return self.ingestion_submission
        return None

    async def tick(self, actor: Principal, namespace_id: UUID, limit: int = 25) -> DispatchBatch:
        operations = self.operations
        claims = await asyncio.to_thread(operations.claim_delivery, actor, namespace_id, limit)
        acknowledged = deferred = quarantined = unconfigured = 0
        for claim in claims:
            try:
                outcome = await asyncio.to_thread(
                    operations.receive_workflow_event, actor, namespace_id, claim.event_id
                )
                if outcome.outcome == "quarantined":
                    await asyncio.to_thread(
                        operations.acknowledge_delivery,
                        actor,
                        namespace_id,
                        claim.event_id,
                        claim.claim_id,
                    )
                    acknowledged += 1
                    quarantined += 1
                    continue
                state = await asyncio.to_thread(
                    operations.get, actor, namespace_id, claim.operation_id
                )
                submission = self._submission(state.kind)
                if submission is None and state.kind in SAMPLE_INGESTION_KINDS | {"ingest_cleanup"}:
                    # Known capabilities can be configured later. Keep inbox/dispatch facts
                    # replayable rather than permanently quarantining valid admitted work.
                    deferred += 1
                    unconfigured += 1
                    continue
                if claim.event_type == "operation.reconciliation_requested":
                    if claim.payload["epoch"] == state.epoch and submission is not None:
                        try:
                            await submission.reconcile_run(
                                actor, namespace_id, state.id, state.epoch
                            )
                        except NotFound:
                            pass  # No provider admission exists; queued event still owns admission.
                    await asyncio.to_thread(
                        operations.acknowledge_delivery,
                        actor,
                        namespace_id,
                        claim.event_id,
                        claim.claim_id,
                    )
                    acknowledged += 1
                    continue
                if (
                    outcome.dispatch is not None
                    and submission is not None
                    and outcome.dispatch.status == "unknown"
                    and outcome.dispatch.epoch != state.epoch
                ):
                    reconciled = await submission.submit(
                        actor,
                        namespace_id,
                        claim.operation_id,
                        outcome.dispatch.epoch,
                        reconcile_only=True,
                    )
                    outcome = outcome.model_copy(update={"dispatch": reconciled})
                if (
                    state.status == "cancellation_requested"
                    and state.kind == "verify_sample_artifact"
                ):
                    try:
                        await submission.submit(
                            actor, namespace_id, state.id, state.epoch, reconcile_only=True
                        )
                    except NotFound:
                        pass  # Cancellation may precede creation of any workflow dispatch.
                    await asyncio.to_thread(
                        operations.cancel_verification, actor, namespace_id, state.id, state.epoch
                    )
                elif (
                    state.status == "cancellation_requested"
                    and state.kind in SAMPLE_INGESTION_KINDS | {"ingest_cleanup"}
                    and submission is not None
                ):
                    try:
                        await submission.submit(
                            actor, namespace_id, state.id, state.epoch, reconcile_only=True
                        )
                    except NotFound:
                        pass
                    if state.kind in SAMPLE_INGESTION_KINDS:
                        await asyncio.to_thread(
                            self.ingestion.ensure_cleanup,
                            actor,
                            namespace_id,
                            state.id,
                            state.epoch,
                        )
                    else:
                        await asyncio.to_thread(
                            operations.confirm_cancel, actor, namespace_id, state.id, state.epoch
                        )
                elif (
                    state.status == "succeeded"
                    and state.kind in SAMPLE_INGESTION_KINDS
                    and submission is not None
                ):
                    await asyncio.to_thread(
                        self.ingestion.ensure_cleanup, actor, namespace_id, state.id, state.epoch
                    )
                elif outcome.dispatch is not None:
                    if submission is None:
                        # Preserve unsupported work as durable quarantine without a hot claim loop.
                        await asyncio.to_thread(
                            operations.quarantine_workflow_kind, actor, namespace_id, claim.event_id
                        )
                        await asyncio.to_thread(
                            operations.acknowledge_delivery,
                            actor,
                            namespace_id,
                            claim.event_id,
                            claim.claim_id,
                        )
                        acknowledged += 1
                        quarantined += 1
                        continue
                    if state.epoch == outcome.dispatch.epoch and state.status == "queued":
                        await submission.submit(
                            actor, namespace_id, claim.operation_id, outcome.dispatch.epoch
                        )
                    elif outcome.dispatch.status == "unknown":
                        # Record old provider facts without reissuing work for a stale attempt.
                        reconciled = await submission.submit(
                            actor,
                            namespace_id,
                            claim.operation_id,
                            outcome.dispatch.epoch,
                            reconcile_only=True,
                        )
                        if reconciled.status == "unknown":
                            deferred += 1
                            continue
                await asyncio.to_thread(
                    operations.acknowledge_delivery,
                    actor,
                    namespace_id,
                    claim.event_id,
                    claim.claim_id,
                )
                acknowledged += 1
                quarantined += outcome.outcome == "quarantined"
            except (WorkflowProviderUnavailable, Conflict):
                # Leave the lease unacknowledged. Redelivery reuses the inbox and exact invocation.
                # A stale acknowledgment can never acknowledge another dispatcher's lease.
                deferred += 1
        return DispatchBatch(len(claims), acknowledged, deferred, quarantined, unconfigured)


async def serve(
    namespace_ids: list[UUID] | None,
    deployment_id: UUID,
    ingestion_deployment_id: UUID | None = None,
):
    """Run a single bounded dispatcher, with graceful native process shutdown."""
    import logging
    import os
    import signal

    from prefect.client.orchestration import get_client

    from verdant.application import RegistryService
    from verdant.flows import WorkerIdentityUnavailable, service_principal
    from verdant.persistence import make_sessions

    logger = logging.getLogger("verdant.dispatch")
    sessions = make_sessions(os.environ["VERDANT_DATABASE_URL"])
    registry = RegistryService(sessions)
    namespace_cursor = None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        async with get_client() as client:
            dispatcher = WorkflowDispatcher(
                sessions, client, deployment_id, ingestion_deployment_id
            )
            while not stop.is_set():
                try:
                    actor = await asyncio.to_thread(service_principal, sessions)
                    batch_namespaces = namespace_ids
                    if batch_namespaces is None:
                        page = await asyncio.to_thread(
                            registry.list_namespaces, actor, namespace_cursor, 100
                        )
                        namespace_cursor = page.next_after
                        batch_namespaces = [
                            item.id for item in page.items if item.access == "write"
                        ]
                    for namespace_id in batch_namespaces:
                        if stop.is_set():
                            break
                        try:
                            result = await dispatcher.tick(actor, namespace_id)
                        except Exception as exc:
                            logger.warning(
                                "workflow delivery failed namespace=%s error=%s",
                                namespace_id,
                                type(exc).__name__,
                            )
                            continue
                        if result.claimed:
                            logger.info(
                                "workflow delivery namespace=%s claimed=%s acknowledged=%s "
                                "deferred=%s quarantined=%s unconfigured=%s",
                                namespace_id,
                                result.claimed,
                                result.acknowledged,
                                result.deferred,
                                result.quarantined,
                                result.unconfigured,
                            )
                except WorkerIdentityUnavailable:
                    logger.warning("Dispatcher identity unavailable; no work admitted")
                except Exception as exc:
                    logger.warning("Dispatcher discovery unavailable error=%s", type(exc).__name__)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2)
                except TimeoutError:
                    pass
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        sessions.kw["bind"].dispose()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Deliver authorized Verdant operations to Prefect")
    parser.add_argument("--namespace", type=UUID, action="append", required=True)
    parser.add_argument("--deployment", type=UUID, required=True)
    parser.add_argument("--ingestion-deployment", type=UUID)
    args = parser.parse_args()
    asyncio.run(
        serve(list(dict.fromkeys(args.namespace)), args.deployment, args.ingestion_deployment)
    )


if __name__ == "__main__":
    main()
