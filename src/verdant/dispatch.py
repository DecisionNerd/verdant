"""Bounded outbox delivery to native Prefect; PostgreSQL remains authoritative."""

import asyncio
from dataclasses import dataclass
from uuid import UUID

from verdant.domain import Conflict, NotFound, Principal
from verdant.operations import OperationService
from verdant.prefect_submission import PrefectSubmission, WorkflowProviderUnavailable


@dataclass(frozen=True)
class DispatchBatch:
    claimed: int
    acknowledged: int
    deferred: int
    quarantined: int


class WorkflowDispatcher:
    def __init__(self, sessions, client, deployment_id: UUID):
        self.operations = OperationService(sessions)
        self.submission = PrefectSubmission(sessions, client, deployment_id)

    async def tick(self, actor: Principal, namespace_id: UUID, limit: int = 25) -> DispatchBatch:
        operations = self.operations
        claims = await asyncio.to_thread(operations.claim_delivery, actor, namespace_id, limit)
        acknowledged = deferred = quarantined = 0
        for claim in claims:
            try:
                outcome = await asyncio.to_thread(
                    operations.receive_workflow_event, actor, namespace_id, claim.event_id
                )
                state = await asyncio.to_thread(
                    operations.get, actor, namespace_id, claim.operation_id
                )
                if (
                    state.status == "cancellation_requested"
                    and state.kind == "verify_sample_artifact"
                ):
                    try:
                        await self.submission.submit(
                            actor, namespace_id, state.id, state.epoch, reconcile_only=True
                        )
                    except NotFound:
                        pass  # Cancellation may precede creation of any workflow dispatch.
                    await asyncio.to_thread(
                        operations.cancel_verification, actor, namespace_id, state.id, state.epoch
                    )
                elif outcome.dispatch is not None:
                    if state.kind != "verify_sample_artifact":
                        # Unsupported work stays durable and visible; never run it as verification.
                        deferred += 1
                        continue
                    if state.epoch == outcome.dispatch.epoch and state.status == "queued":
                        await self.submission.submit(
                            actor, namespace_id, claim.operation_id, outcome.dispatch.epoch
                        )
                    elif outcome.dispatch.status == "unknown":
                        # An old or cancelled attempt may still have a provider write to reconcile.
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
        return DispatchBatch(len(claims), acknowledged, deferred, quarantined)


async def serve(namespace_ids: list[UUID], deployment_id: UUID):
    """Run a single bounded dispatcher, with graceful native process shutdown."""
    import logging
    import os
    import signal

    from prefect.client.orchestration import get_client

    from verdant.flows import WorkerIdentityUnavailable, service_principal
    from verdant.persistence import make_sessions

    logger = logging.getLogger("verdant.dispatch")
    sessions = make_sessions(os.environ["VERDANT_DATABASE_URL"])
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        async with get_client() as client:
            dispatcher = WorkflowDispatcher(sessions, client, deployment_id)
            while not stop.is_set():
                try:
                    actor = await asyncio.to_thread(service_principal, sessions)
                    for namespace_id in namespace_ids:
                        if stop.is_set():
                            break
                        result = await dispatcher.tick(actor, namespace_id)
                        if result.claimed:
                            logger.info(
                                "workflow delivery namespace=%s claimed=%s acknowledged=%s "
                                "deferred=%s quarantined=%s",
                                namespace_id,
                                result.claimed,
                                result.acknowledged,
                                result.deferred,
                                result.quarantined,
                            )
                except WorkerIdentityUnavailable:
                    logger.warning("Dispatcher identity unavailable; no work admitted")
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
    args = parser.parse_args()
    asyncio.run(serve(list(dict.fromkeys(args.namespace)), args.deployment))


if __name__ == "__main__":
    main()
