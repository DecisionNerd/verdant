"""Owned staging cleanup with durable receipts and conservative write reconciliation.

Final artifact objects are never deleted here. Missing provider facts keep cancellation
pending rather than treating an interrupted write as absent.
"""

import hashlib

from sqlalchemy import select

from verdant.contracts import SAMPLE_INGESTION_KINDS, Contract
from verdant.domain import Conflict, NotFound
from verdant.ingestion import IngestionBatch, IngestionService
from verdant.operations import EffectReconciliation, EffectRequest, EffectResult
from verdant.persistence import (
    ArtifactLocationRow,
    ArtifactRow,
    EffectReceiptRow,
    IngestionItemRow,
    WorkflowDispatchRow,
)
from verdant.storage import ObjectCorrupt, ObjectDigest, ObjectMissing
from verdant.telemetry import observed

CLEANUP_STATES = frozenset({"cancellation_requested", "cancelled", "succeeded"})


class CleanupProgress(Contract):
    batch: IngestionBatch
    cleaned_items: int
    remaining_items: int
    pending_reconciliation: bool
    next_after_position: int | None = None


class IngestionCleanup:
    def __init__(self, sessions, storage, *, store_id="primary"):
        self.sessions, self.storage = sessions, storage
        self.ingestion = IngestionService(
            sessions, bucket=storage.settings.bucket, store_id=store_id, storage=storage
        )
        self.operations = self.ingestion.operations

    def request_cancel(self, actor, namespace_id, operation_id, epoch, *, expired_only=False):
        return self.ingestion.request_cancel(
            actor, namespace_id, operation_id, epoch, expired_only=expired_only
        )

    def _snapshot(self, session, actor, namespace_id, operation_id, item_id, epoch):
        operation = self.operations._locked_operation(
            session, actor, namespace_id, operation_id, epoch
        )
        if operation.kind not in SAMPLE_INGESTION_KINDS or operation.status not in CLEANUP_STATES:
            raise Conflict("Operation does not permit staging cleanup")
        item = session.scalar(
            select(IngestionItemRow)
            .where(
                IngestionItemRow.namespace_id == namespace_id,
                IngestionItemRow.operation_id == operation_id,
                IngestionItemRow.id == item_id,
            )
            .with_for_update()
        )
        if item is None:
            raise NotFound("Ingestion item not found")
        artifact = session.get(ArtifactRow, item.artifact_id)
        location = session.scalar(
            select(ArtifactLocationRow).where(
                ArtifactLocationRow.namespace_id == namespace_id,
                ArtifactLocationRow.artifact_id == artifact.id,
                ArtifactLocationRow.store_id == self.ingestion.store_id,
                ArtifactLocationRow.bucket == self.ingestion.bucket,
            )
        )
        if location is None:
            raise Conflict("Recorded cleanup storage is unavailable")
        if (
            item.staging_key != f"{namespace_id}/staging/{item.id}"
            or item.staging_key == location.object_key
        ):
            raise Conflict("Cleanup requires the domain-owned staging location")
        receipts = session.scalars(
            select(EffectReceiptRow)
            .where(
                EffectReceiptRow.namespace_id == namespace_id,
                EffectReceiptRow.operation_id == operation_id,
                EffectReceiptRow.identity["item"].as_string() == str(item_id),
            )
            .limit(11)
        ).all()
        if len(receipts) > 10:
            raise Conflict("Item effect history exceeds bounded cleanup")
        return item, artifact, location, receipts

    def _read(self, actor, namespace_id, operation_id, item_id, epoch):
        with self.sessions.begin() as session:
            return self._snapshot(session, actor, namespace_id, operation_id, item_id, epoch)

    def _found(self, actor, namespace_id, operation_id, item_id, epoch, receipt, result):
        with self.sessions.begin() as session:
            self._snapshot(session, actor, namespace_id, operation_id, item_id, epoch)
            current = self.operations._locked_effect(
                session, namespace_id, operation_id, receipt.id
            )
            if current.status == "succeeded" and (
                current.result_digest == result.result_digest
                and current.provider_reference == result.provider_reference
            ):
                return
            self.operations._reconcile_effect(
                session,
                actor,
                namespace_id,
                operation_id,
                receipt.id,
                EffectReconciliation(
                    expected_revision=receipt.revision,
                    outcome="found",
                    result=result,
                    evidence="Owned staging cleanup observed the exact provider outcome",
                ),
            )

    def _settle_writes(self, actor, namespace_id, operation_id, item_id, epoch):
        item, artifact, location, receipts = self._read(
            actor, namespace_id, operation_id, item_id, epoch
        )
        by_kind = {receipt.identity["kind"]: receipt for receipt in receipts}
        upload_id = item.upload_id
        initiation = by_kind.get("artifact.multipart.initiate")
        if initiation is not None:
            expected = {"bucket": self.ingestion.bucket, "key": item.staging_key}
            if (
                initiation.request != expected
                or initiation.identity["input_revision"] != artifact.sha256
            ):
                raise Conflict("Cleanup has an unexpected initiation receipt")
            if initiation.status == "unknown":
                uploads = self.storage.find_uploads(item.staging_key)
                if len(uploads) != 1:
                    return False
                upload_id = uploads[0]
                self._found(
                    actor,
                    namespace_id,
                    operation_id,
                    item_id,
                    epoch,
                    initiation,
                    EffectResult(
                        result_digest=hashlib.sha256(upload_id.encode()).hexdigest(),
                        provider_reference=upload_id,
                    ),
                )
            elif initiation.status == "succeeded":
                upload_id = initiation.provider_reference
        completion = by_kind.get("artifact.multipart.complete")
        if completion is not None and completion.status == "unknown":
            expected = {
                "bucket": self.ingestion.bucket,
                "key": item.staging_key,
                "upload_id": upload_id,
                "size_bytes": artifact.size_bytes,
            }
            if not upload_id or completion.request != expected:
                raise Conflict("Cleanup has an unexpected completion receipt")
            try:
                self.storage.size(item.staging_key)
            except ObjectMissing:
                return False
            self._found(
                actor,
                namespace_id,
                operation_id,
                item_id,
                epoch,
                completion,
                EffectResult(
                    result_digest=hashlib.sha256(upload_id.encode()).hexdigest(),
                    provider_reference=upload_id,
                ),
            )
        promotion = by_kind.get("artifact.promote")
        if promotion is not None and promotion.status == "unknown":
            with self.sessions.begin() as session:
                parent = self.operations._locked_operation(
                    session, actor, namespace_id, operation_id, epoch
                )
                source = self.ingestion._promotion_source(
                    parent, item, artifact, location, require_configuration=False
                )
            expected = source.promotion(self.ingestion.bucket, self.ingestion.store_id)
            if promotion.identity != expected.identity() or promotion.request != expected.request:
                raise Conflict("Cleanup has an unexpected promotion receipt")
            try:
                self.storage.verify(
                    location.object_key, ObjectDigest(artifact.sha256, artifact.size_bytes)
                )
            except (ObjectMissing, ObjectCorrupt):
                return False
            self._found(
                actor,
                namespace_id,
                operation_id,
                item_id,
                epoch,
                promotion,
                EffectResult(
                    result_digest=artifact.sha256,
                    provider_reference=str(artifact.id),
                ),
            )
        _, _, _, current = self._read(actor, namespace_id, operation_id, item_id, epoch)
        return not any(
            r.status == "unknown" and r.identity["kind"] != "artifact.staging.cleanup"
            for r in current
        )

    def _cleanup_command(self, item, artifact):
        return EffectRequest(
            kind="artifact.staging.cleanup",
            item=str(item.id),
            input_revision=artifact.sha256,
            request={
                "store_id": self.ingestion.store_id,
                "bucket": self.ingestion.bucket,
                "staging_key": item.staging_key,
            },
        )

    @staticmethod
    def _cleanup_result(command):
        return EffectResult(
            result_digest=hashlib.sha256(command.request["staging_key"].encode()).hexdigest(),
            provider_reference=command.item,
        )

    def _completed(self, session, operation_id, commands):
        receipts = session.scalars(
            select(EffectReceiptRow)
            .where(
                EffectReceiptRow.operation_id == operation_id,
                EffectReceiptRow.identity["kind"].as_string() == "artifact.staging.cleanup",
                EffectReceiptRow.status == "succeeded",
            )
            .limit(101)
        ).all()
        if len(receipts) > 100:
            raise Conflict("Cleanup effect history exceeds its bounded batch")
        completed = set()
        for receipt in receipts:
            key = receipt.identity.get("item")
            command = commands.get(key)
            if command is not None:
                result = self._cleanup_result(command)
                if (
                    receipt.identity == command.identity()
                    and receipt.request == command.request
                    and receipt.result_digest == result.result_digest
                    and receipt.provider_reference == result.provider_reference
                ):
                    completed.add(key)
        return completed

    def _cleanup_item(self, actor, namespace_id, operation_id, item_id, epoch):
        if not self._settle_writes(actor, namespace_id, operation_id, item_id, epoch):
            return False
        with self.sessions.begin() as session:
            item, artifact, _, _ = self._snapshot(
                session, actor, namespace_id, operation_id, item_id, epoch
            )
            command = self._cleanup_command(item, artifact)
            receipt = self.operations._prepare_effect(
                session,
                actor,
                namespace_id,
                operation_id,
                epoch,
                command,
                allowed_statuses=CLEANUP_STATES,
            )
            if receipt.status == "succeeded":
                result = self._cleanup_result(command)
                if (
                    receipt.result_digest != result.result_digest
                    or receipt.provider_reference != result.provider_reference
                ):
                    raise Conflict("Cleanup receipt does not match its exact staging outcome")
                return True
            if receipt.status == "prepared":
                receipt = self.operations._begin_effect(
                    session,
                    actor,
                    namespace_id,
                    operation_id,
                    epoch,
                    receipt.id,
                    allowed_statuses=CLEANUP_STATES,
                )
        # Replaying these exact scoped abort/delete actions is natively idempotent.
        self._read(actor, namespace_id, operation_id, item_id, epoch)
        uploads = self.storage.find_uploads(item.staging_key)
        if item.upload_id and item.upload_id not in uploads:
            uploads.append(item.upload_id)
        for upload_id in uploads:
            self.storage.abort(item.staging_key, upload_id)
        self.storage.delete_one(item.staging_key)
        if self.storage.find_uploads(item.staging_key):
            return False
        try:
            self.storage.size(item.staging_key)
        except ObjectMissing:
            self._found(
                actor,
                namespace_id,
                operation_id,
                item_id,
                epoch,
                receipt,
                self._cleanup_result(command),
            )
            return True
        return False

    @observed("ingestion")
    def run(self, actor, namespace_id, operation_id, epoch, *, limit=25, after_position=None):
        if type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError("Cleanup batch must contain between 1 and 25 items")
        if after_position is not None and (
            type(after_position) is not int or not 0 <= after_position <= 99
        ):
            raise ValueError("Cleanup cursor is outside the bounded operation")
        with self.sessions.begin() as session:
            operation = self.operations._locked_operation(
                session, actor, namespace_id, operation_id, epoch
            )
            if (
                operation.kind not in SAMPLE_INGESTION_KINDS
                or operation.status not in CLEANUP_STATES
            ):
                raise Conflict("Operation does not permit staging cleanup")
            rows = session.execute(
                select(IngestionItemRow, ArtifactRow)
                .join(
                    ArtifactRow,
                    (ArtifactRow.id == IngestionItemRow.artifact_id)
                    & (ArtifactRow.namespace_id == IngestionItemRow.namespace_id),
                )
                .where(
                    IngestionItemRow.namespace_id == namespace_id,
                    IngestionItemRow.operation_id == operation_id,
                )
                .order_by(IngestionItemRow.position)
                .limit(100)
            ).all()
            ids = [item.id for item, artifact in rows]
            commands = {
                str(item.id): self._cleanup_command(item, artifact) for item, artifact in rows
            }
            completed = self._completed(session, operation_id, commands)
            positions = {item.id: item.position for item, artifact in rows}
        candidates = [
            item_id
            for item_id in ids
            if str(item_id) not in completed
            and (after_position is None or positions[item_id] > after_position)
        ]
        page = candidates[:limit]
        next_after = positions[page[-1]] if len(candidates) > limit else None
        pending = False
        for item_id in page:
            if not self._cleanup_item(actor, namespace_id, operation_id, item_id, epoch):
                pending = True
        with self.sessions.begin() as session:
            operation = self.operations._locked_operation(
                session, actor, namespace_id, operation_id, epoch
            )
            completed = self._completed(session, operation_id, commands)
            remaining = len(ids) - len(completed)
            unknown = session.scalar(
                select(EffectReceiptRow.id)
                .where(
                    EffectReceiptRow.operation_id == operation_id,
                    EffectReceiptRow.status == "unknown",
                )
                .limit(1)
            )
            dispatch = session.get(WorkflowDispatchRow, (operation_id, epoch))
            pending = (
                pending
                or unknown is not None
                or (dispatch is not None and dispatch.status == "unknown")
            )
            if remaining == 0 and operation.status == "cancellation_requested" and not pending:
                items = session.scalars(
                    select(IngestionItemRow)
                    .where(
                        IngestionItemRow.operation_id == operation_id,
                        IngestionItemRow.state.not_in(["accepted", "superseded", "cancelled"]),
                    )
                    .with_for_update()
                ).all()
                for item in items:
                    item.state = "cancelled"
                if operation.revision == 9223372036854775807:
                    raise Conflict("Operation revision limit reached")
                operation.status = "cancelled"
                operation.revision += 1
                self.operations._record_transition(session, actor, operation)
        return CleanupProgress(
            batch=self.ingestion.get(actor, namespace_id, operation_id),
            cleaned_items=len(completed),
            remaining_items=remaining,
            pending_reconciliation=bool(pending),
            next_after_position=next_after,
        )
