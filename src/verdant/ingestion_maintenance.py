"""Native maintenance attempts admit execution; parent receipts own cleanup outcomes."""

from sqlalchemy import select

from verdant.domain import Conflict, DomainError, NotFound
from verdant.ingestion_cleanup import IngestionCleanup
from verdant.persistence import (
    ArtifactRow,
    EffectReceiptRow,
    IngestionCleanupTaskRow,
    IngestionItemRow,
    WorkflowDispatchRow,
)
from verdant.storage import StorageError
from verdant.telemetry import observed


class IngestionMaintenance:
    def __init__(self, sessions, storage):
        self.sessions = sessions
        self.cleanup = IngestionCleanup(sessions, storage)
        self.operations = self.cleanup.operations

    def target(self, actor, namespace_id, operation_id, epoch):
        with self.sessions.begin() as session:
            operation = self.operations._locked_operation(
                session, actor, namespace_id, operation_id, epoch
            )
            task = session.get(IngestionCleanupTaskRow, operation_id)
            if task is None or task.namespace_id != namespace_id:
                raise NotFound("Cleanup admission target not found")
            if operation.kind != "ingest_cleanup" or operation.inputs != {
                "parent_operation_id": str(task.parent_operation_id),
                "parent_epoch": task.parent_epoch,
            }:
                raise Conflict("Cleanup operation does not match its immutable target")
            return task

    def _finish(self, actor, namespace_id, operation_id, epoch, task):
        with self.sessions.begin() as session:
            # Parent precedes maintenance operation in lock order, matching cleanup admission.
            parent = self.operations._locked_operation(
                session, actor, namespace_id, task.parent_operation_id, task.parent_epoch
            )
            operation = self.operations._locked_operation(
                session, actor, namespace_id, operation_id, epoch
            )
            if operation.status == "succeeded":
                return self.operations._view(operation)
            if operation.status != "running":
                raise Conflict("Maintenance attempt no longer permits completion")
            rows = session.execute(
                select(IngestionItemRow, ArtifactRow)
                .join(
                    ArtifactRow,
                    (ArtifactRow.id == IngestionItemRow.artifact_id)
                    & (ArtifactRow.namespace_id == IngestionItemRow.namespace_id),
                )
                .where(
                    IngestionItemRow.namespace_id == namespace_id,
                    IngestionItemRow.operation_id == parent.id,
                )
                .limit(100)
            ).all()
            commands = {
                str(item.id): self.cleanup._cleanup_command(item, artifact)
                for item, artifact in rows
            }
            completed = self.cleanup._completed(session, parent.id, commands)
            unknown = session.scalar(
                select(EffectReceiptRow.id)
                .where(
                    EffectReceiptRow.operation_id == parent.id,
                    EffectReceiptRow.status == "unknown",
                )
                .limit(1)
            )
            dispatch = session.get(WorkflowDispatchRow, (parent.id, task.parent_epoch))
            pending = (
                len(completed) != len(rows)
                or unknown is not None
                or (dispatch is not None and dispatch.status == "unknown")
                or parent.status not in {"succeeded", "cancelled"}
            )
            if operation.revision == 9223372036854775807:
                raise Conflict("Operation revision limit reached")
            operation.status = "failed" if pending else "succeeded"
            operation.revision += 1
            self.operations._record_transition(
                session,
                actor,
                operation,
                extra={
                    "parent_operation_id": str(parent.id),
                    "parent_epoch": task.parent_epoch,
                    "error": "cleanup_pending_reconciliation" if pending else None,
                },
            )
            return self.operations._view(operation)

    @observed("ingestion")
    def run(self, actor, namespace_id, operation_id, epoch):
        task = self.target(actor, namespace_id, operation_id, epoch)
        state = self.operations.get(actor, namespace_id, operation_id)
        if state.epoch != epoch:
            raise Conflict("Maintenance attempt is stale")
        if state.status == "succeeded":
            return state
        if state.status == "cancellation_requested":
            return self.operations.confirm_cancel(actor, namespace_id, operation_id, epoch)
        if state.status == "cancelled":
            return state
        self.operations.start(actor, namespace_id, operation_id, epoch)
        cursor = None
        try:
            for _ in range(4):
                current = self.operations.get(actor, namespace_id, operation_id)
                if current.epoch != epoch or current.status != "running":
                    raise Conflict("Maintenance attempt no longer permits cleanup")
                progress = self.cleanup.run(
                    actor,
                    namespace_id,
                    task.parent_operation_id,
                    task.parent_epoch,
                    limit=25,
                    after_position=cursor,
                )
                cursor = progress.next_after_position
                if cursor is None:
                    break
            return self._finish(actor, namespace_id, operation_id, epoch, task)
        except StorageError:
            try:
                self.operations.fail(actor, namespace_id, operation_id, epoch)
            except DomainError:
                pass
            raise
        except Conflict:
            current = self.operations.get(actor, namespace_id, operation_id)
            if current.epoch == epoch:
                if current.status == "succeeded":
                    return current
                if current.status == "cancellation_requested":
                    return self.operations.confirm_cancel(actor, namespace_id, operation_id, epoch)
                if current.status == "running":
                    try:
                        self.operations.fail(actor, namespace_id, operation_id, epoch)
                    except DomainError:
                        pass
            raise
