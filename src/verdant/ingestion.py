"""Namespace-owned ingestion declarations composed with durable operation admission."""

import hashlib
from datetime import timedelta
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, model_validator
from sqlalchemy import func, select

from verdant.contracts import Contract, Digest, Revision
from verdant.domain import Conflict, NotFound, Principal
from verdant.identity import current_principal
from verdant.operations import (
    EffectReconciliation,
    EffectRequest,
    EffectResult,
    Operation,
    OperationRequest,
    OperationService,
)
from verdant.persistence import (
    ArtifactLocationRow,
    ArtifactRow,
    EffectReceiptRow,
    IngestionItemRow,
    OperationRow,
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


class IngestionItem(Contract):
    id: UUID
    artifact_id: UUID
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


class UploadedPart(Contract):
    part_number: int
    size_bytes: int


class UploadProgress(Contract):
    parts: list[UploadedPart]
    part_bytes: int = PART_BYTES


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
        return IngestionBatch(
            operation=OperationService._view(operation),
            items=[
                IngestionItem(
                    id=item.id,
                    artifact_id=item.artifact_id,
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
                select(OperationRow).where(
                    OperationRow.namespace_id == namespace_id,
                    OperationRow.id == operation_id,
                    OperationRow.kind == "ingest_upload",
                )
            )
            if row is None:
                raise NotFound("Ingestion operation not found")
            return self._view(session, row)

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
