"""Bounded-memory artifact read verification over durable operation/effect services."""

import hashlib
from uuid import UUID

from verdant.domain import Conflict, DomainError, Principal
from verdant.operations import (
    EffectReconciliation,
    EffectRequest,
    EffectResult,
    OperationService,
)
from verdant.telemetry import observed


class VerificationFailed(Exception):
    """Safe worker error; private object keys and provider response bodies are omitted."""


class _Cancelled(Exception):
    pass


class VerificationRunner:
    def __init__(self, sessions, s3, bucket: str):
        self.operations = OperationService(sessions)
        self.s3 = s3
        self.bucket = bucket

    def _check(self, actor, namespace_id, operation_id, epoch):
        state = self.operations.get(actor, namespace_id, operation_id)
        if state.epoch != epoch:
            raise Conflict("Operation attempt is stale")
        if state.status == "cancellation_requested":
            raise _Cancelled()
        if state.status != "running":
            raise Conflict("Verification is no longer running")

    @observed("verification")
    def run(self, actor: Principal, namespace_id: UUID, operation_id: UUID, epoch: int):
        operations = self.operations
        state = operations.get(actor, namespace_id, operation_id)
        if state.epoch != epoch:
            raise Conflict("Operation attempt is stale")
        if state.status == "succeeded":
            return state
        if state.status == "cancellation_requested":
            return operations.confirm_cancel(actor, namespace_id, operation_id, epoch)
        operations.start(actor, namespace_id, operation_id, epoch)
        source = operations.verification_source(actor, namespace_id, operation_id, epoch)
        receipt = operations.prepare_effect(
            actor,
            namespace_id,
            operation_id,
            epoch,
            EffectRequest(
                kind="artifact.read",
                item=str(source.revision_id),
                input_revision=source.sha256,
                request={
                    "bucket": self.bucket,
                    "object_key": source.object_key,
                    "sha256": source.sha256,
                    "size_bytes": source.size_bytes,
                },
            ),
        )
        if receipt.status == "succeeded":
            return operations.finalize_verification(actor, namespace_id, operation_id, epoch)
        reconciling = receipt.status == "unknown"
        if not reconciling:
            receipt = operations.begin_effect(actor, namespace_id, operation_id, epoch, receipt.id)
        try:
            self._check(actor, namespace_id, operation_id, epoch)
            # This provider action is read-only: replaying the read is its reconciliation protocol.
            response = self.s3.get_object(Bucket=self.bucket, Key=source.object_key)
            body = response["Body"]
            digest, size, chunks = hashlib.sha256(), 0, 0
            try:
                if response["ContentLength"] != source.size_bytes:
                    raise VerificationFailed("Artifact size does not match registered revision")
                while chunk := body.read(1024 * 1024):
                    size += len(chunk)
                    if size > source.size_bytes:
                        raise VerificationFailed("Artifact exceeds registered size")
                    digest.update(chunk)
                    chunks += 1
                    if chunks % 16 == 0:
                        self._check(actor, namespace_id, operation_id, epoch)
            finally:
                body.close()
            self._check(actor, namespace_id, operation_id, epoch)
            if size != source.size_bytes or digest.hexdigest() != source.sha256:
                raise VerificationFailed("Artifact bytes do not match registered revision")
            result = EffectResult(
                result_digest=digest.hexdigest(), provider_reference=str(source.revision_id)
            )
            if reconciling:
                operations.reconcile_effect(
                    actor,
                    namespace_id,
                    operation_id,
                    receipt.id,
                    EffectReconciliation(
                        expected_revision=receipt.revision,
                        outcome="found",
                        result=result,
                        evidence="Read-only artifact replay matched exact registered bytes",
                    ),
                )
            else:
                operations.complete_effect(
                    actor,
                    namespace_id,
                    operation_id,
                    epoch,
                    receipt.id,
                    receipt.dispatch_id,
                    result,
                )
            return operations.finalize_verification(actor, namespace_id, operation_id, epoch)
        except _Cancelled:
            # A cancelled read has no external write to leave ambiguous.
            operations.reconcile_effect(
                actor,
                namespace_id,
                operation_id,
                receipt.id,
                EffectReconciliation(
                    expected_revision=receipt.revision,
                    outcome="absent",
                    evidence="Read-only verification cancelled before accepting a result",
                ),
            )
            return operations.confirm_cancel(actor, namespace_id, operation_id, epoch)
        except DomainError:
            raise
        except Exception as exc:
            try:
                operations.fail(actor, namespace_id, operation_id, epoch)
            except DomainError:
                pass  # A newer epoch, revocation or terminal outcome remains authoritative.
            if isinstance(exc, VerificationFailed):
                raise
            raise VerificationFailed("Artifact verification provider is unavailable") from None
