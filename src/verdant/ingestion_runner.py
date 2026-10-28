"""Bounded ingestion worker; provider bytes are verified outside database transactions."""

from verdant.contracts import SAMPLE_INGESTION_KINDS
from verdant.domain import Conflict, DomainError
from verdant.ingestion import IngestionService, RegistrationConflict
from verdant.storage import ObjectCorrupt, ObjectDigest, ObjectMissing, StorageError
from verdant.telemetry import observed


class IngestionFailed(Exception):
    """Safe worker failure without private placement or provider response details."""


class IngestionRunner:
    def __init__(self, sessions, storage, *, store_id="primary"):
        self.storage = storage
        self.ingestion = IngestionService(
            sessions, bucket=storage.settings.bucket, store_id=store_id, storage=storage
        )
        self.operations = self.ingestion.operations

    def _fail(self, actor, namespace_id, operation_id, item_id, epoch, code):
        try:
            self.ingestion.fail_verification(
                actor, namespace_id, operation_id, item_id, epoch, code
            )
        except DomainError:
            pass  # Revocation, cancellation or a newer attempt remains authoritative.

    @observed("ingestion")
    def run(self, actor, namespace_id, operation_id, epoch, *, limit=25):
        state = self.operations.get(actor, namespace_id, operation_id)
        if state.epoch != epoch:
            raise Conflict("Operation attempt is stale")
        if state.kind not in SAMPLE_INGESTION_KINDS:
            raise Conflict("Unsupported ingestion operation kind")
        if state.status == "succeeded":
            return self.ingestion.get(actor, namespace_id, operation_id)
        try:
            items = self.ingestion.verification_items(
                actor, namespace_id, operation_id, epoch, limit
            )
            self.operations.start(actor, namespace_id, operation_id, epoch)
        except Conflict:
            current = self.ingestion.get(actor, namespace_id, operation_id)
            if current.operation.epoch == epoch and current.operation.status == "succeeded":
                return current
            raise
        for item_id in items:
            try:
                source = self.ingestion.verification_source(
                    actor, namespace_id, operation_id, item_id, epoch
                )
                receipt = self.operations.prepare_effect(
                    actor,
                    namespace_id,
                    operation_id,
                    epoch,
                    source.promotion(self.ingestion.bucket, self.ingestion.store_id),
                )
                if receipt.status == "prepared":
                    try:
                        self.operations.begin_effect(
                            actor, namespace_id, operation_id, epoch, receipt.id
                        )
                    except Conflict:
                        # Another worker may have dispatched this same logical effect.
                        self.operations.prepare_effect(
                            actor,
                            namespace_id,
                            operation_id,
                            epoch,
                            source.promotion(self.ingestion.bucket, self.ingestion.store_id),
                        )
                # Reconcile final bytes first on every attempt, including previously succeeded
                # effects. Missing/corrupt objects never become usable merely from a DB receipt.
                self.ingestion.verification_source(
                    actor, namespace_id, operation_id, item_id, epoch
                )
                self.storage.promote(
                    source.staging_key,
                    source.final_key,
                    ObjectDigest(source.sha256, source.size_bytes),
                )
                self.ingestion.accept_verified_item(
                    actor, namespace_id, operation_id, item_id, epoch, receipt.id
                )
            except StorageError as exc:
                code = (
                    "artifact_missing"
                    if isinstance(exc, ObjectMissing)
                    else "artifact_corrupt"
                    if isinstance(exc, ObjectCorrupt)
                    else "storage_unavailable"
                )
                self._fail(actor, namespace_id, operation_id, item_id, epoch, code)
                raise IngestionFailed(code) from None
            except RegistrationConflict:
                self._fail(
                    actor, namespace_id, operation_id, item_id, epoch, "registration_conflict"
                )
                raise
            except Conflict:
                current = self.ingestion.get(actor, namespace_id, operation_id)
                if current.operation.epoch == epoch:
                    if current.operation.status == "succeeded":
                        return current
                    if current.operation.status == "running" and any(
                        item.id == item_id and item.state == "accepted" for item in current.items
                    ):
                        continue
                raise
        return self.ingestion.get(actor, namespace_id, operation_id)
