"""Namespace-owned ingestion declarations composed with durable operation admission."""

import hashlib
import json
from datetime import timedelta
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from verdant.application import RegistryService
from verdant.contracts import Contract, Digest, Revision, SampleContent
from verdant.domain import Conflict, NotFound, Principal
from verdant.identity import current_principal
from verdant.operations import (
    EffectReconciliation,
    EffectRequest,
    EffectResult,
    Operation,
    OperationAction,
    OperationRequest,
    OperationService,
)
from verdant.persistence import (
    ArtifactLocationRow,
    ArtifactRow,
    EffectReceiptRow,
    IngestionItemRow,
    OperationRow,
    RegistryRepository,
    SampleArtifactRow,
    SampleRow,
)
from verdant.storage import (
    MAX_ARTIFACT_BYTES,
    PART_BYTES,
    URL_SECONDS,
    ObjectCorrupt,
    ObjectMissing,
    S3Storage,
    StorageUnavailable,
)
from verdant.telemetry import observed

UPLOAD_TTL = timedelta(hours=1)


class UploadDeclaration(Contract):
    source_key: str = Field(min_length=1, max_length=500)
    modality: str = Field(min_length=1, max_length=50)
    sha256: Digest
    size_bytes: int = Field(ge=0, le=MAX_ARTIFACT_BYTES, strict=True)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    target_sample_id: UUID | None = None
    expected_revision: Revision | None = None
    retry_of_item_id: UUID | None = None

    @model_validator(mode="after")
    def revision_target(self):
        if (self.target_sample_id is None) != (self.expected_revision is None):
            raise ValueError("Sample replacement requires sample identity and expected revision")
        if self.expected_revision is not None and self.expected_revision < 1:
            raise ValueError("Sample replacement requires an existing revision")
        return self


class UploadRequest(Contract):
    request_key: str = Field(min_length=1, max_length=200)
    items: list[UploadDeclaration] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def bounded_identity(self):
        if len({item.source_key for item in self.items}) != len(self.items):
            raise ValueError("A batch cannot repeat a source identity")
        targets = [item.target_sample_id for item in self.items if item.target_sample_id]
        if len(set(targets)) != len(targets):
            raise ValueError("A batch cannot replace the same sample twice")
        self.operation_request()  # Apply the shared UTF-8 64-KiB operation-input bound.
        return self

    def operation_request(self):
        return OperationRequest(
            request_key=self.request_key,
            kind="ingest_upload",
            inputs={"items": [item.model_dump(mode="json") for item in self.items]},
        )


class ReuploadRequest(Contract):
    request_key: str = Field(min_length=1, max_length=200)
    item_ids: list[UUID] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def distinct_items(self):
        if len(set(self.item_ids)) != len(self.item_ids):
            raise ValueError("Recovery cannot repeat an item identity")
        return self


class IngestionItem(Contract):
    id: UUID
    artifact_id: UUID
    retry_of_item_id: UUID | None
    replacement_operation_id: UUID | None = None
    replacement_item_id: UUID | None = None
    position: int
    source_key: str
    modality: str
    sha256: str
    size_bytes: int
    state: str
    error: str | None
    sample_id: UUID | None
    accepted_revision_id: UUID | None


class IngestionBatch(Contract):
    operation: Operation
    items: list[IngestionItem]


class SignedUploadPart(Contract):
    part_number: int
    size_bytes: int
    url: str = Field(repr=False)
    expires_in: int = URL_SECONDS


class UploadPartRequest(OperationAction):
    part_number: int = Field(ge=1, le=32, strict=True)


class UploadLimits(Contract):
    configured: bool
    max_artifact_bytes: int = MAX_ARTIFACT_BYTES
    max_items: int = 100
    part_bytes: int = PART_BYTES
    signed_url_seconds: int = URL_SECONDS


class UploadedPart(Contract):
    part_number: int
    size_bytes: int


class UploadProgress(Contract):
    parts: list[UploadedPart]
    part_bytes: int = PART_BYTES


class IngestionSource(Contract):
    item_id: UUID
    artifact_id: UUID
    staging_key: str
    final_key: str
    sha256: str
    size_bytes: int

    def promotion(self, bucket: str, store_id: str):
        return EffectRequest(
            kind="artifact.promote",
            item=str(self.item_id),
            input_revision=self.sha256,
            request={
                "store_id": store_id,
                "bucket": bucket,
                "staging_key": self.staging_key,
                "final_key": self.final_key,
                "sha256": self.sha256,
                "size_bytes": self.size_bytes,
            },
        )


class RegistrationConflict(Conflict):
    """Explicit source/revision conflict requiring a new registration decision."""


class IngestionService:
    def __init__(
        self, sessions, *, bucket: str, store_id: str = "primary", storage: S3Storage | None = None
    ):
        if not bucket or len(bucket) > 255 or not store_id or len(store_id) > 100:
            raise ValueError("Invalid configured ingestion store")
        self.sessions = sessions
        self.operations = OperationService(sessions)
        self.bucket = bucket
        self.store_id = store_id
        if storage is not None and storage.settings.bucket != bucket:
            raise ValueError("Ingestion storage does not match configured bucket")
        self.storage = storage

    def require_storage(self, actor, namespace_id):
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id, write=True)
        if self.storage is None:
            raise StorageUnavailable("Upload storage is not configured")

    def limits(self, actor, namespace_id):
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id)
        return UploadLimits(configured=self.storage is not None)

    @staticmethod
    def _view(session, operation):
        rows = session.execute(
            select(IngestionItemRow, ArtifactRow)
            .join(
                ArtifactRow,
                (ArtifactRow.id == IngestionItemRow.artifact_id)
                & (ArtifactRow.namespace_id == IngestionItemRow.namespace_id),
            )
            .where(
                IngestionItemRow.namespace_id == operation.namespace_id,
                IngestionItemRow.operation_id == operation.id,
            )
            .order_by(IngestionItemRow.position)
            .limit(100)
        ).all()
        replacements = {}
        if rows:
            children = session.scalars(
                select(IngestionItemRow)
                .where(
                    IngestionItemRow.namespace_id == operation.namespace_id,
                    IngestionItemRow.retry_of_item_id.in_([item.id for item, artifact in rows]),
                )
                .limit(100)
            ).all()
            replacements = {child.retry_of_item_id: child for child in children}
        return IngestionBatch(
            operation=OperationService._view(operation),
            items=[
                IngestionItem(
                    id=item.id,
                    artifact_id=item.artifact_id,
                    retry_of_item_id=item.retry_of_item_id,
                    replacement_operation_id=replacements[item.id].operation_id
                    if item.id in replacements
                    else None,
                    replacement_item_id=replacements[item.id].id
                    if item.id in replacements
                    else None,
                    position=item.position,
                    source_key=item.source_key,
                    modality=item.modality,
                    sha256=artifact.sha256,
                    size_bytes=artifact.size_bytes,
                    state=item.state,
                    error=item.error,
                    sample_id=item.sample_id,
                    accepted_revision_id=item.accepted_revision_id,
                )
                for item, artifact in rows
            ],
        )

    def _retry_origins(self, session, actor, namespace_id, command):
        declarations = {
            item.retry_of_item_id: item for item in command.items if item.retry_of_item_id
        }
        if not declarations:
            return {}
        origins = session.scalars(
            select(IngestionItemRow).where(
                IngestionItemRow.namespace_id == namespace_id,
                IngestionItemRow.id.in_(declarations),
            )
        ).all()
        if len(origins) != len(declarations):
            raise NotFound("Re-upload origin not found")
        # Parent operations precede their items in lock order. Sort multi-parent recovery batches.
        parents = {
            operation_id: self.operations._locked_operation(
                session, actor, namespace_id, operation_id
            )
            for operation_id in sorted({item.operation_id for item in origins}, key=str)
        }
        for parent_id, parent in parents.items():
            count = sum(item.operation_id == parent_id for item in origins)
            if parent.revision > 9223372036854775807 - count:
                raise Conflict("Operation revision limit reached")
        result = {}
        for origin in origins:
            parent = parents[origin.operation_id]
            session.refresh(origin, with_for_update=True)
            if parent.kind != "ingest_upload" or parent.status not in {"failed", "cancelled"}:
                raise Conflict("Re-upload requires a failed or cancelled original operation")
            if origin.state in {"accepted", "superseded"}:
                raise Conflict("Accepted or already re-uploaded items cannot be re-uploaded")
            if parent.revision == 9223372036854775807:
                raise Conflict("Operation revision limit reached")
            declaration = declarations[origin.id]
            artifact = session.get(ArtifactRow, origin.artifact_id)
            original = (
                origin.source_key,
                origin.modality,
                artifact.sha256,
                artifact.size_bytes,
                origin.target_sample_id,
                origin.expected_revision,
            )
            requested = (
                declaration.source_key,
                declaration.modality,
                declaration.sha256,
                declaration.size_bytes,
                declaration.target_sample_id,
                declaration.expected_revision,
            )
            if original != requested or json.dumps(
                origin.metadata_json, sort_keys=True
            ) != json.dumps(declaration.metadata, sort_keys=True):
                raise Conflict(
                    "Re-upload must preserve the declaration; register changed inputs separately"
                )
            result[origin.id] = (origin, parent)
        return result

    @observed("ingestion")
    def admit(self, actor: Principal, namespace_id: UUID, command: UploadRequest):
        with self.sessions.begin() as session:
            operation = self.operations._admit(
                session, actor, namespace_id, command.operation_request(), initial_status="running"
            )
            row = self.operations._locked_operation(session, actor, namespace_id, operation.id)
            prior = session.scalar(
                select(IngestionItemRow.id).where(IngestionItemRow.operation_id == row.id).limit(1)
            )
            if prior is not None:
                return self._view(session, row)
            retries = self._retry_origins(session, actor, namespace_id, command)
            expires_at = session.scalar(select(func.clock_timestamp())) + UPLOAD_TTL
            for position, declaration in enumerate(command.items):
                if declaration.target_sample_id is not None:
                    target = session.scalar(
                        select(SampleRow).where(
                            SampleRow.namespace_id == namespace_id,
                            SampleRow.id == declaration.target_sample_id,
                        )
                    )
                    if target is None:
                        raise NotFound("Replacement sample not found")
                    if target.revision != declaration.expected_revision:
                        raise Conflict("Replacement sample revision changed")
                    if (
                        target.source_key != declaration.source_key
                        or target.modality != declaration.modality
                    ):
                        raise Conflict(
                            "Replacement must preserve sample source identity and modality"
                        )
                elif (
                    session.scalar(
                        select(SampleRow.id).where(
                            SampleRow.namespace_id == namespace_id,
                            SampleRow.source_key == declaration.source_key,
                        )
                    )
                    is not None
                ):
                    raise Conflict("Source identity already exists; select sample replacement")
                artifact_id, item_id = uuid4(), uuid4()
                if declaration.retry_of_item_id is not None:
                    original, parent = retries[declaration.retry_of_item_id]
                    original.state = "superseded"
                    parent.revision += 1
                    self.operations._record_transition(
                        session,
                        actor,
                        parent,
                        "operation.ingestion_item_superseded",
                        {
                            "item_id": str(original.id),
                            "replacement_operation_id": str(row.id),
                            "replacement_item_id": str(item_id),
                        },
                    )
                session.add(
                    ArtifactRow(
                        id=artifact_id,
                        namespace_id=namespace_id,
                        sha256=declaration.sha256,
                        size_bytes=declaration.size_bytes,
                    )
                )
                session.flush()
                session.add(
                    ArtifactLocationRow(
                        namespace_id=namespace_id,
                        artifact_id=artifact_id,
                        store_id=self.store_id,
                        bucket=self.bucket,
                        object_key=f"{namespace_id}/artifacts/{artifact_id}",
                    )
                )
                session.add(
                    IngestionItemRow(
                        id=item_id,
                        namespace_id=namespace_id,
                        operation_id=row.id,
                        retry_of_item_id=declaration.retry_of_item_id,
                        position=position,
                        artifact_id=artifact_id,
                        source_key=declaration.source_key,
                        modality=declaration.modality,
                        metadata_json=declaration.metadata,
                        target_sample_id=declaration.target_sample_id,
                        expected_revision=declaration.expected_revision,
                        staging_key=f"{namespace_id}/staging/{item_id}",
                        expires_at=expires_at,
                    )
                )
            session.flush()
            return self._view(session, row)

    @observed("ingestion")
    def get(self, actor: Principal, namespace_id: UUID, operation_id: UUID):
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id)
            row = session.scalar(
                select(OperationRow)
                .where(
                    OperationRow.namespace_id == namespace_id,
                    OperationRow.id == operation_id,
                    OperationRow.kind == "ingest_upload",
                )
                .with_for_update(read=True)
            )
            if row is None:
                raise NotFound("Ingestion operation not found")
            return self._view(session, row)

    @observed("ingestion")
    def reupload(self, actor, namespace_id, command: ReuploadRequest):
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id, write=True)
            rows = session.execute(
                select(IngestionItemRow, ArtifactRow)
                .join(
                    ArtifactRow,
                    (ArtifactRow.id == IngestionItemRow.artifact_id)
                    & (ArtifactRow.namespace_id == IngestionItemRow.namespace_id),
                )
                .where(
                    IngestionItemRow.namespace_id == namespace_id,
                    IngestionItemRow.id.in_(command.item_ids),
                )
                .limit(100)
            ).all()
            if len(rows) != len(command.item_ids):
                raise NotFound("Re-upload origin not found")
            by_id = {item.id: (item, artifact) for item, artifact in rows}
            declarations = []
            for item_id in command.item_ids:
                item, artifact = by_id[item_id]
                declarations.append(
                    UploadDeclaration(
                        source_key=item.source_key,
                        modality=item.modality,
                        sha256=artifact.sha256,
                        size_bytes=artifact.size_bytes,
                        metadata=item.metadata_json,
                        target_sample_id=item.target_sample_id,
                        expected_revision=item.expected_revision,
                        retry_of_item_id=item.id,
                    )
                )
            try:
                request = UploadRequest(request_key=command.request_key, items=declarations)
            except ValidationError:
                raise Conflict(
                    "Recovery inputs conflict or exceed limits; select fewer items"
                ) from None
        # Declaration fields are immutable; admission rechecks current grants and origin state
        # under parent/item locks before committing the replacement operation.
        return self.admit(actor, namespace_id, request)

    def _upload_item(
        self, session, actor, namespace_id, operation_id, item_id, epoch, *, allow_ready=False
    ):
        operation = self.operations._locked_operation(
            session, actor, namespace_id, operation_id, epoch
        )
        statuses = {"running", "queued"} if allow_ready else {"running"}
        if operation.kind != "ingest_upload" or operation.status not in statuses:
            raise Conflict("Operation does not permit upload changes")
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
            raise NotFound("Upload item not found")
        if allow_ready and item.state == "ready":
            return operation, item, session.get(ArtifactRow, item.artifact_id)
        if operation.status != "running" or item.state not in {"awaiting_upload", "uploading"}:
            raise Conflict("Item does not permit upload changes")
        if item.expires_at <= session.scalar(select(func.clock_timestamp())):
            raise Conflict("Upload expired; create a new upload declaration")
        artifact = session.get(ArtifactRow, item.artifact_id)
        location = session.scalar(
            select(ArtifactLocationRow.id).where(
                ArtifactLocationRow.namespace_id == namespace_id,
                ArtifactLocationRow.artifact_id == item.artifact_id,
                ArtifactLocationRow.store_id == self.store_id,
                ArtifactLocationRow.bucket == self.bucket,
            )
        )
        if location is None:
            raise StorageUnavailable("Stored upload location is unavailable in this configuration")
        return operation, item, artifact

    @staticmethod
    def _completion_status(session, operation_id, item_id):
        return session.scalar(
            select(EffectReceiptRow.status)
            .where(
                EffectReceiptRow.operation_id == operation_id,
                EffectReceiptRow.identity["item"].as_string() == str(item_id),
                EffectReceiptRow.identity["kind"].as_string() == "artifact.multipart.complete",
                EffectReceiptRow.status.in_(["unknown", "succeeded"]),
            )
            .limit(1)
        )

    def _upload_snapshot(self, actor, namespace_id, operation_id, item_id, epoch, *, signing=False):
        with self.sessions.begin() as session:
            _, item, artifact = self._upload_item(
                session, actor, namespace_id, operation_id, item_id, epoch
            )
            if self.storage is None:
                raise StorageUnavailable("Upload storage is not configured")
            completion = None
            if signing:
                completion = self._completion_status(session, operation_id, item_id)
            if completion == "succeeded":
                raise Conflict("Completion is in progress; reconcile it before signing more parts")
        if completion == "unknown":
            # Repair is safe only while the exact native upload still exists. Keep uncertainty:
            # an older completion may still be in flight, and final bytes require verification.
            try:
                self.storage.parts(item.staging_key, item.upload_id)
            except ObjectMissing:
                raise Conflict(
                    "Completion is in progress; reconcile it before signing more parts"
                ) from None
            with self.sessions.begin() as session:
                _, item, artifact = self._upload_item(
                    session, actor, namespace_id, operation_id, item_id, epoch
                )
                if self._completion_status(session, operation_id, item_id) == "succeeded":
                    raise Conflict(
                        "Completion is in progress; reconcile it before signing more parts"
                    )
        return item, artifact

    @observed("ingestion")
    def initiate_upload(self, actor, namespace_id, operation_id, item_id, epoch):
        item, artifact = self._upload_snapshot(actor, namespace_id, operation_id, item_id, epoch)
        receipt = self.operations.prepare_effect(
            actor,
            namespace_id,
            operation_id,
            epoch,
            EffectRequest(
                kind="artifact.multipart.initiate",
                item=str(item.id),
                input_revision=artifact.sha256,
                request={"bucket": self.bucket, "key": item.staging_key},
            ),
        )
        if receipt.status == "prepared":
            receipt = self.operations.begin_effect(
                actor, namespace_id, operation_id, epoch, receipt.id
            )
            # Recheck current grants/cancellation after persisting uncertainty, before provider I/O.
            self._upload_snapshot(actor, namespace_id, operation_id, item_id, epoch)
            upload_id = self.storage.initiate(item.staging_key)
            self.operations.complete_effect(
                actor,
                namespace_id,
                operation_id,
                epoch,
                receipt.id,
                receipt.dispatch_id,
                EffectResult(
                    result_digest=hashlib.sha256(upload_id.encode()).hexdigest(),
                    provider_reference=upload_id,
                ),
            )
        elif receipt.status == "unknown":
            uploads = self.storage.find_uploads(item.staging_key)
            if len(uploads) != 1:
                # Absence cannot prove a timed-out initiation is no longer in flight.
                raise Conflict("Upload initiation is uncertain; retry reconciliation")
            upload_id = uploads[0]
            self.operations.reconcile_effect(
                actor,
                namespace_id,
                operation_id,
                receipt.id,
                EffectReconciliation(
                    expected_revision=receipt.revision,
                    outcome="found",
                    result=EffectResult(
                        result_digest=hashlib.sha256(upload_id.encode()).hexdigest(),
                        provider_reference=upload_id,
                    ),
                    evidence="Exact domain-allocated staging key has one active multipart upload",
                ),
            )
        else:
            upload_id = receipt.provider_reference
        with self.sessions.begin() as session:
            operation, current, _ = self._upload_item(
                session, actor, namespace_id, operation_id, item_id, epoch
            )
            if current.upload_id not in {None, upload_id}:
                raise Conflict("Upload identity conflicts with committed provider receipt")
            current.upload_id = upload_id
            current.state = "uploading"
            session.flush()
            return self._view(session, operation)

    @observed("ingestion")
    def sign_upload_part(self, actor, namespace_id, operation_id, item_id, epoch, number: int):
        item, artifact = self._upload_snapshot(
            actor, namespace_id, operation_id, item_id, epoch, signing=True
        )
        count = max(1, (artifact.size_bytes + PART_BYTES - 1) // PART_BYTES)
        if type(number) is not int or not 1 <= number <= count:
            raise Conflict("Part number is outside the declared upload")
        if not item.upload_id or item.state != "uploading":
            raise Conflict("Upload has not been initiated")
        size = min(PART_BYTES, max(0, artifact.size_bytes - (number - 1) * PART_BYTES))
        url = self.storage.sign_part(item.staging_key, item.upload_id, number, size)
        self._upload_snapshot(actor, namespace_id, operation_id, item_id, epoch, signing=True)
        return SignedUploadPart(part_number=number, size_bytes=size, url=url)

    @observed("ingestion")
    def upload_progress(self, actor, namespace_id, operation_id, item_id, epoch):
        item, _ = self._upload_snapshot(actor, namespace_id, operation_id, item_id, epoch)
        if not item.upload_id:
            return UploadProgress(parts=[])
        parts = self.storage.parts(item.staging_key, item.upload_id)
        self._upload_snapshot(actor, namespace_id, operation_id, item_id, epoch)
        return UploadProgress(
            parts=[
                UploadedPart(part_number=part["PartNumber"], size_bytes=part["Size"])
                for part in parts
            ]
        )

    @observed("ingestion")
    def complete_upload(self, actor, namespace_id, operation_id, item_id, epoch):
        with self.sessions.begin() as session:
            operation, item, artifact = self._upload_item(
                session, actor, namespace_id, operation_id, item_id, epoch, allow_ready=True
            )
            if item.state == "ready":
                return self._view(session, operation)
            if not item.upload_id or item.state != "uploading":
                raise Conflict("Upload has not been initiated")
            if self.storage is None:
                raise StorageUnavailable("Upload storage is not configured")
        receipt = self.operations.prepare_effect(
            actor,
            namespace_id,
            operation_id,
            epoch,
            EffectRequest(
                kind="artifact.multipart.complete",
                item=str(item.id),
                input_revision=artifact.sha256,
                request={
                    "bucket": self.bucket,
                    "key": item.staging_key,
                    "upload_id": item.upload_id,
                    "size_bytes": artifact.size_bytes,
                },
            ),
        )
        result = EffectResult(
            result_digest=hashlib.sha256(item.upload_id.encode()).hexdigest(),
            provider_reference=item.upload_id,
        )
        if receipt.status == "prepared":
            receipt = self.operations.begin_effect(
                actor, namespace_id, operation_id, epoch, receipt.id
            )
            self._upload_snapshot(actor, namespace_id, operation_id, item_id, epoch)
            try:
                self.storage.complete(item.staging_key, item.upload_id, artifact.size_bytes)
            except ObjectCorrupt:
                # The adapter rejected the part layout before calling provider completion.
                self.operations.reconcile_effect(
                    actor,
                    namespace_id,
                    operation_id,
                    receipt.id,
                    EffectReconciliation(
                        expected_revision=receipt.revision,
                        outcome="absent",
                        evidence="Part-layout preflight rejected completion before provider write",
                    ),
                )
                raise Conflict(
                    "Uploaded parts are incomplete or invalid; resume the upload"
                ) from None
            self.operations.complete_effect(
                actor, namespace_id, operation_id, epoch, receipt.id, receipt.dispatch_id, result
            )
        elif receipt.status == "unknown":
            try:
                self.storage.size(item.staging_key)
            except ObjectMissing:
                # This exact native upload ID can complete once; never create a replacement here.
                self.storage.parts(item.staging_key, item.upload_id)
                self._upload_snapshot(actor, namespace_id, operation_id, item_id, epoch)
                try:
                    self.storage.complete(item.staging_key, item.upload_id, artifact.size_bytes)
                except ObjectCorrupt:
                    # Preserve unknown state; only repair parts of this same active upload.
                    raise Conflict(
                        "Uploaded parts are incomplete or invalid; resume the upload"
                    ) from None
            self.operations.reconcile_effect(
                actor,
                namespace_id,
                operation_id,
                receipt.id,
                EffectReconciliation(
                    expected_revision=receipt.revision,
                    outcome="found",
                    result=result,
                    evidence="Staging object exists for the exact native multipart upload",
                ),
            )
        size = self.storage.size(item.staging_key)
        with self.sessions.begin() as session:
            operation, current, _ = self._upload_item(
                session, actor, namespace_id, operation_id, item_id, epoch, allow_ready=True
            )
            if current.state == "ready":
                return self._view(session, operation)
            current.state = "ready" if size == artifact.size_bytes else "failed"
            current.error = None if current.state == "ready" else "size_mismatch"
            session.flush()
            unfinished = session.scalar(
                select(IngestionItemRow.id)
                .where(
                    IngestionItemRow.operation_id == operation_id,
                    IngestionItemRow.state != "ready",
                )
                .limit(1)
            )
            if operation.revision == 9223372036854775807:
                raise Conflict("Operation revision limit reached")
            operation.revision += 1
            if unfinished is None:
                operation.status = "queued"
                event = "operation.queued"
            else:
                event = (
                    "operation.upload_ready"
                    if current.state == "ready"
                    else "operation.upload_failed"
                )
            self.operations._record_transition(
                session, actor, operation, event, {"item_id": str(item_id)}
            )
            return self._view(session, operation)

    @observed("ingestion")
    def verification_items(self, actor, namespace_id, operation_id, epoch, limit: int = 25):
        if type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError("Verification batch must contain between 1 and 25 items")
        with self.sessions.begin() as session:
            operation = self.operations._locked_operation(
                session, actor, namespace_id, operation_id, epoch
            )
            if operation.kind != "ingest_upload" or operation.status not in {"queued", "running"}:
                raise Conflict("Operation does not permit ingestion verification")
            pending = session.scalar(
                select(IngestionItemRow.id)
                .where(
                    IngestionItemRow.operation_id == operation_id,
                    IngestionItemRow.state.not_in(["ready", "accepted"]),
                )
                .limit(1)
            )
            if pending is not None:
                raise Conflict("All uploads must be ready before worker verification")
            return list(
                session.scalars(
                    select(IngestionItemRow.id)
                    .where(
                        IngestionItemRow.operation_id == operation_id,
                        IngestionItemRow.state == "ready",
                    )
                    .order_by(IngestionItemRow.position)
                    .limit(limit)
                )
            )

    def _verification_source(self, session, actor, namespace_id, operation_id, item_id, epoch):
        operation = self.operations._locked_operation(
            session, actor, namespace_id, operation_id, epoch
        )
        if operation.kind != "ingest_upload" or operation.status not in {"running", "succeeded"}:
            raise Conflict("Operation does not permit sample acceptance")
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
        if item.state not in {"ready", "accepted"}:
            raise Conflict("Item is not ready for verification")
        artifact = session.scalar(
            select(ArtifactRow)
            .where(
                ArtifactRow.namespace_id == namespace_id,
                ArtifactRow.id == item.artifact_id,
            )
            .with_for_update()
        )
        location = session.scalar(
            select(ArtifactLocationRow).where(
                ArtifactLocationRow.namespace_id == namespace_id,
                ArtifactLocationRow.artifact_id == artifact.id,
                ArtifactLocationRow.store_id == self.store_id,
                ArtifactLocationRow.bucket == self.bucket,
            )
        )
        if location is None:
            raise StorageUnavailable(
                "Stored artifact location is unavailable in this configuration"
            )
        source = IngestionSource(
            item_id=item.id,
            artifact_id=artifact.id,
            staging_key=item.staging_key,
            final_key=location.object_key,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
        )
        return operation, item, artifact, source

    @observed("ingestion")
    def verification_source(self, actor, namespace_id, operation_id, item_id, epoch):
        with self.sessions.begin() as session:
            return self._verification_source(
                session, actor, namespace_id, operation_id, item_id, epoch
            )[3]

    @observed("ingestion")
    def accept_verified_item(self, actor, namespace_id, operation_id, item_id, epoch, receipt_id):
        """Worker-only boundary, called after independently verifying final object bytes.

        No HTTP/MCP endpoint may turn caller-declared digests into verification evidence.
        """
        with self.sessions.begin() as session:
            operation, item, artifact, source = self._verification_source(
                session, actor, namespace_id, operation_id, item_id, epoch
            )
            if item.state == "accepted":
                return self._view(session, operation)
            if operation.status != "running":
                raise Conflict("Operation does not permit sample acceptance")
            if operation.revision > 9223372036854775805:
                raise Conflict("Operation revision limit reached")
            receipt = self.operations._locked_effect(
                session, namespace_id, operation_id, receipt_id
            )
            expected = source.promotion(self.bucket, self.store_id)
            if receipt.identity != expected.identity() or receipt.request != expected.request:
                raise Conflict("Acceptance requires its exact artifact promotion receipt")
            result = EffectResult(
                result_digest=artifact.sha256, provider_reference=str(artifact.id)
            )
            if receipt.status == "unknown":
                self.operations._reconcile_effect(
                    session,
                    actor,
                    namespace_id,
                    operation_id,
                    receipt_id,
                    EffectReconciliation(
                        expected_revision=receipt.revision,
                        outcome="found",
                        result=result,
                        evidence="Verified final bytes precede atomic sample acceptance",
                    ),
                )
            elif receipt.status != "succeeded" or (
                receipt.result_digest != result.result_digest
                or receipt.provider_reference != result.provider_reference
            ):
                raise Conflict("Artifact promotion has no matching verified result")
            if item.target_sample_id is None:
                sample_id = session.scalar(
                    insert(SampleRow)
                    .values(
                        id=uuid4(),
                        namespace_id=namespace_id,
                        source_key=item.source_key,
                        modality=item.modality,
                        revision=1,
                    )
                    .on_conflict_do_nothing(constraint="uq_sample_source")
                    .returning(SampleRow.id)
                )
                if sample_id is None:
                    raise RegistrationConflict(
                        "Source identity now exists; select explicit sample replacement"
                    )
                sample = session.get(SampleRow, sample_id)
                action = "sample.created"
            else:
                sample = session.scalar(
                    select(SampleRow)
                    .where(
                        SampleRow.namespace_id == namespace_id,
                        SampleRow.id == item.target_sample_id,
                    )
                    .with_for_update()
                )
                if sample is None:
                    raise NotFound("Replacement sample not found")
                if sample.revision != item.expected_revision:
                    raise RegistrationConflict(
                        "Replacement sample revision changed before acceptance"
                    )
                if sample.source_key != item.source_key or sample.modality != item.modality:
                    raise RegistrationConflict("Replacement sample identity changed")
                if sample.revision == 9223372036854775807:
                    raise Conflict("Sample revision limit reached")
                sample.revision += 1
                action = "sample.revised"
            if artifact.verified_at is None:
                artifact.verified_at = session.scalar(select(func.clock_timestamp()))
            repo = RegistryRepository(session)
            revision = RegistryService._add_revision(
                repo,
                sample,
                SampleContent(
                    sha256=artifact.sha256,
                    size_bytes=artifact.size_bytes,
                    object_key=source.final_key,
                    metadata=item.metadata_json,
                ),
            )
            session.add(
                SampleArtifactRow(
                    namespace_id=namespace_id,
                    sample_id=sample.id,
                    revision_id=revision.id,
                    artifact_id=artifact.id,
                )
            )
            session.flush()
            RegistryService._audit(
                repo,
                actor,
                namespace_id,
                sample.id,
                action,
                {
                    "revision_id": str(revision.id),
                    "revision": revision.revision,
                    "operation_id": str(operation_id),
                    "item_id": str(item_id),
                    "artifact_id": str(artifact.id),
                },
            )
            item.state, item.error = "accepted", None
            item.sample_id, item.accepted_revision_id = sample.id, revision.id
            session.flush()
            remaining = session.scalar(
                select(IngestionItemRow.id)
                .where(
                    IngestionItemRow.operation_id == operation_id,
                    IngestionItemRow.state != "accepted",
                )
                .limit(1)
            )
            if remaining is None:
                unknown = session.scalar(
                    select(EffectReceiptRow.id)
                    .where(
                        EffectReceiptRow.operation_id == operation_id,
                        EffectReceiptRow.status == "unknown",
                    )
                    .limit(1)
                )
                if unknown is not None:
                    raise Conflict(
                        "Unknown effects require reconciliation before ingestion finishes"
                    )
                operation.status = "succeeded"
            operation.revision += 1
            self.operations._record_transition(
                session,
                actor,
                operation,
                "operation.succeeded" if remaining is None else "operation.ingestion_item_accepted",
                {"item_id": str(item_id), "sample_revision_id": str(revision.id)},
            )
            return self._view(session, operation)

    @observed("ingestion")
    def fail_verification(self, actor, namespace_id, operation_id, item_id, epoch, error: str):
        if error not in {
            "artifact_missing",
            "artifact_corrupt",
            "storage_unavailable",
            "registration_conflict",
        }:
            raise ValueError("Unsupported safe ingestion failure code")
        with self.sessions.begin() as session:
            operation = self.operations._locked_operation(
                session, actor, namespace_id, operation_id, epoch
            )
            item = session.scalar(
                select(IngestionItemRow)
                .where(
                    IngestionItemRow.namespace_id == namespace_id,
                    IngestionItemRow.operation_id == operation_id,
                    IngestionItemRow.id == item_id,
                )
                .with_for_update()
            )
            if (
                operation.kind != "ingest_upload"
                or operation.status != "running"
                or item is None
                or item.state != "ready"
            ):
                raise Conflict("Ingestion failure is stale")
            if operation.revision == 9223372036854775807:
                raise Conflict("Operation revision limit reached")
            item.error = error
            operation.status = "failed"
            operation.revision += 1
            self.operations._record_transition(
                session,
                actor,
                operation,
                extra={
                    "item_id": str(item_id),
                    "error": error,
                },
            )
            return self._view(session, operation)

    @observed("ingestion")
    def request_cancel(self, actor, namespace_id, operation_id, epoch, *, expired_only=False):
        with self.sessions.begin() as session:
            operation = self.operations._locked_operation(
                session, actor, namespace_id, operation_id, epoch
            )
            if operation.kind != "ingest_upload":
                raise Conflict("Unsupported ingestion operation kind")
            if operation.status in {"cancellation_requested", "cancelled"}:
                return self._view(session, operation)
            if operation.status not in {"running", "queued", "failed"}:
                raise Conflict("Operation does not permit cancellation")
            if expired_only:
                expired = session.scalar(
                    select(IngestionItemRow.id)
                    .where(
                        IngestionItemRow.operation_id == operation_id,
                        IngestionItemRow.state.in_(["awaiting_upload", "uploading"]),
                        IngestionItemRow.expires_at <= func.clock_timestamp(),
                    )
                    .limit(1)
                )
                if expired is None:
                    raise Conflict("Operation has no expired incomplete upload")
            if operation.revision == 9223372036854775807:
                raise Conflict("Operation revision limit reached")
            operation.status = "cancellation_requested"
            operation.revision += 1
            self.operations._record_transition(
                session,
                actor,
                operation,
                extra={"reason": "upload_expired" if expired_only else "user_requested"},
            )
            return self._view(session, operation)
