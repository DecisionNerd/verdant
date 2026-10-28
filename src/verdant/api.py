"""FastAPI composition over shared services. Identity verification is supplied by #11."""

import os
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from verdant import contracts as c
from verdant.application import RegistryService
from verdant.auth import IdentityRuntime, IdentitySettings
from verdant.contracts import SAMPLE_INGESTION_KINDS
from verdant.domain import DomainError, Forbidden, NotFound, Principal
from verdant.ingestion import (
    IngestionBatch,
    IngestionService,
    ReuploadRequest,
    SignedUploadPart,
    SourceRegistrationRequest,
    UploadLimits,
    UploadPartRequest,
    UploadProgress,
    UploadRequest,
)
from verdant.logging import configure_access_logging
from verdant.operations import (
    Operation,
    OperationAction,
    OperationDetail,
    OperationPage,
    OperationService,
    VerificationRequest,
)
from verdant.persistence import make_sessions
from verdant.preview import PreviewService, SamplePreview
from verdant.storage import ObjectCorrupt, ObjectMissing, S3Storage, StorageError


def verified_principal() -> Principal:
    """Fail closed until the Keycloak verifier is installed; no header/password shortcut."""
    raise HTTPException(status_code=401, detail="Verified identity required")


Actor = Annotated[Principal, Depends(verified_principal)]


def create_app(
    sessions: sessionmaker[Session] | None = None,
    identity_settings: IdentitySettings | None = None,
    *,
    storage: S3Storage | None = None,
) -> FastAPI:
    configure_access_logging()
    sessions = sessions or make_sessions(os.environ["VERDANT_DATABASE_URL"])
    service = RegistryService(sessions)
    operations = OperationService(sessions)
    owns_storage = storage is None
    storage = storage or S3Storage.from_env()
    ingestion = IngestionService(
        sessions, bucket=storage.settings.bucket if storage else "unconfigured", storage=storage
    )
    app = FastAPI(title="Verdant platform", version=str(c.CONTRACT_VERSION))
    app.state.storage = storage
    previews = PreviewService(sessions, storage)
    identity_settings = identity_settings or IdentitySettings.from_env()
    if identity_settings is not None:
        IdentityRuntime(sessions, identity_settings).install(app, verified_principal)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        code = 403 if isinstance(exc, Forbidden) else 404 if isinstance(exc, NotFound) else 409
        return JSONResponse(status_code=code, content={"detail": str(exc)})

    @app.exception_handler(StorageError)
    async def storage_error(request: Request, exc: StorageError):
        code = (
            404
            if isinstance(exc, ObjectMissing)
            else 409
            if isinstance(exc, ObjectCorrupt)
            else 503
        )
        return JSONResponse(
            status_code=code, content={"detail": str(exc)}, headers={"Cache-Control": "no-store"}
        )

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready():
        try:
            with sessions() as session:
                revision = session.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != c.SCHEMA_REVISION:
                raise RuntimeError("schema mismatch")
        except Exception:
            raise HTTPException(
                status_code=503, detail="Required database/schema unavailable"
            ) from None
        return {
            "status": "ready",
            "contract_version": c.CONTRACT_VERSION,
            "event_version": c.EVENT_VERSION,
            "manifest_version": c.MANIFEST_VERSION,
            "schema_revision": revision,
            "identity": "unqualified",
        }

    @app.get("/api/v1/namespaces", response_model=c.NamespacePage)
    def list_namespaces(
        actor: Actor, after: UUID | None = None, limit: int = Query(default=50, ge=1, le=100)
    ):
        return service.list_namespaces(actor, after, limit)

    @app.get("/api/v1/namespaces/{namespace_id}", response_model=c.Namespace)
    def get_namespace(namespace_id: UUID, actor: Actor):
        return service.get_namespace(actor, namespace_id)

    prefix = "/api/v1/namespaces/{namespace_id}"

    @app.get(
        prefix + "/samples/{sample_id}/revisions/{revision_id}/preview",
        response_model=SamplePreview,
    )
    def preview_sample(namespace_id: UUID, sample_id: UUID, revision_id: UUID, actor: Actor):
        result = previews.get(actor, namespace_id, sample_id, revision_id)
        return JSONResponse(result.model_dump(mode="json"), headers={"Cache-Control": "no-store"})

    @app.get(prefix + "/ingestions/limits", response_model=UploadLimits)
    def upload_limits(namespace_id: UUID, actor: Actor):
        return ingestion.limits(actor, namespace_id)

    @app.get(prefix + "/ingestions", response_model=OperationPage)
    def list_ingestions(
        namespace_id: UUID,
        actor: Actor,
        after: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ):
        return operations.list_operations(
            actor, namespace_id, after, limit, kind=SAMPLE_INGESTION_KINDS
        )

    @app.post(prefix + "/ingestions/uploads", response_model=IngestionBatch, status_code=202)
    def admit_upload(namespace_id: UUID, command: UploadRequest, actor: Actor):
        ingestion.require_storage(actor, namespace_id)
        return ingestion.admit(actor, namespace_id, command)

    @app.post(prefix + "/ingestions/sources", response_model=IngestionBatch, status_code=202)
    def register_sources(namespace_id: UUID, command: SourceRegistrationRequest, actor: Actor):
        return ingestion.admit_source(actor, namespace_id, command)

    @app.post(prefix + "/ingestions/reuploads", response_model=IngestionBatch, status_code=202)
    def reupload(namespace_id: UUID, command: ReuploadRequest, actor: Actor):
        ingestion.require_storage(actor, namespace_id)
        return ingestion.reupload(actor, namespace_id, command)

    @app.get(prefix + "/ingestions/{operation_id}", response_model=IngestionBatch)
    def get_ingestion(namespace_id: UUID, operation_id: UUID, actor: Actor):
        return ingestion.get(actor, namespace_id, operation_id)

    @app.post(
        prefix + "/ingestions/{operation_id}/items/{item_id}/initiate",
        response_model=IngestionBatch,
    )
    def initiate_upload(
        namespace_id: UUID,
        operation_id: UUID,
        item_id: UUID,
        command: OperationAction,
        actor: Actor,
    ):
        return ingestion.initiate_upload(actor, namespace_id, operation_id, item_id, command.epoch)

    @app.post(
        prefix + "/ingestions/{operation_id}/items/{item_id}/sign-part",
        response_model=SignedUploadPart,
    )
    def sign_upload_part(
        namespace_id: UUID,
        operation_id: UUID,
        item_id: UUID,
        command: UploadPartRequest,
        actor: Actor,
    ):
        return ingestion.sign_upload_part(
            actor, namespace_id, operation_id, item_id, command.epoch, command.part_number
        )

    @app.get(
        prefix + "/ingestions/{operation_id}/items/{item_id}/parts", response_model=UploadProgress
    )
    def upload_progress(
        namespace_id: UUID,
        operation_id: UUID,
        item_id: UUID,
        actor: Actor,
        epoch: int = Query(ge=1, le=9223372036854775807),
    ):
        return ingestion.upload_progress(actor, namespace_id, operation_id, item_id, epoch)

    @app.post(
        prefix + "/ingestions/{operation_id}/items/{item_id}/complete",
        response_model=IngestionBatch,
        status_code=202,
    )
    def complete_upload(
        namespace_id: UUID,
        operation_id: UUID,
        item_id: UUID,
        command: OperationAction,
        actor: Actor,
    ):
        return ingestion.complete_upload(actor, namespace_id, operation_id, item_id, command.epoch)

    @app.post(
        prefix + "/ingestions/{operation_id}/cancel", response_model=IngestionBatch, status_code=202
    )
    def cancel_ingestion(
        namespace_id: UUID, operation_id: UUID, command: OperationAction, actor: Actor
    ):
        return ingestion.request_cancel(actor, namespace_id, operation_id, command.epoch)

    @app.post(prefix + "/operations/verify-artifact", response_model=Operation, status_code=202)
    def verify_artifact(namespace_id: UUID, command: VerificationRequest, actor: Actor):
        return operations.admit_verification(actor, namespace_id, command)

    @app.get(prefix + "/operations", response_model=OperationPage)
    def list_operations(
        namespace_id: UUID,
        actor: Actor,
        after: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ):
        return operations.list_operations(actor, namespace_id, after, limit)

    @app.get(prefix + "/operations/{operation_id}", response_model=OperationDetail)
    def operation_detail(
        namespace_id: UUID,
        operation_id: UUID,
        actor: Actor,
        after: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ):
        return operations.detail(actor, namespace_id, operation_id, after, limit)

    @app.post(prefix + "/operations/{operation_id}/retry", response_model=Operation)
    def retry_operation(
        namespace_id: UUID, operation_id: UUID, command: OperationAction, actor: Actor
    ):
        return operations.retry(actor, namespace_id, operation_id, command.epoch)

    @app.post(prefix + "/operations/{operation_id}/cancel", response_model=Operation)
    def cancel_operation(
        namespace_id: UUID, operation_id: UUID, command: OperationAction, actor: Actor
    ):
        return operations.request_cancel(actor, namespace_id, operation_id, command.epoch)

    @app.post(
        prefix + "/operations/{operation_id}/reconcile", response_model=Operation, status_code=202
    )
    def reconcile_operation(
        namespace_id: UUID, operation_id: UUID, command: OperationAction, actor: Actor
    ):
        return operations.request_reconcile(actor, namespace_id, operation_id, command.epoch)

    @app.get(prefix + "/datasets", response_model=c.DatasetPage)
    def list_datasets(
        namespace_id: UUID,
        actor: Actor,
        search: str = Query(default="", max_length=200),
        modality: str | None = Query(default=None, max_length=50),
        owner_id: UUID | None = None,
        after: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return service.list_datasets(actor, namespace_id, search, modality, owner_id, after, limit)

    @app.post(prefix + "/datasets", response_model=c.Dataset, status_code=201)
    def create_dataset(namespace_id: UUID, command: c.DatasetCreate, actor: Actor):
        return service.create_dataset(actor, namespace_id, command)

    @app.put(prefix + "/datasets/{dataset_id}", response_model=c.Dataset)
    def update_dataset(
        namespace_id: UUID, dataset_id: UUID, command: c.DatasetUpdate, actor: Actor
    ):
        return service.update_dataset(actor, namespace_id, dataset_id, command)

    @app.get(prefix + "/datasets/{dataset_id}", response_model=c.Dataset)
    def get_dataset(namespace_id: UUID, dataset_id: UUID, actor: Actor):
        return service.get_dataset(actor, namespace_id, dataset_id)

    @app.get(prefix + "/samples", response_model=c.SamplePage)
    def list_samples(
        namespace_id: UUID,
        actor: Actor,
        after: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return service.list_samples(actor, namespace_id, after, limit)

    @app.post(prefix + "/samples", response_model=c.SampleRevision, status_code=201)
    def create_sample(namespace_id: UUID, command: c.SampleCreate, actor: Actor):
        return service.create_sample(actor, namespace_id, command)

    @app.post(
        prefix + "/samples/{sample_id}/revisions", response_model=c.SampleRevision, status_code=201
    )
    def revise_sample(namespace_id: UUID, sample_id: UUID, command: c.SampleRevise, actor: Actor):
        return service.revise_sample(actor, namespace_id, sample_id, command)

    @app.get(
        prefix + "/samples/{sample_id}/revisions/{revision_id}/summary",
        response_model=c.SampleSummary,
    )
    def inspect_sample_revision(
        namespace_id: UUID, sample_id: UUID, revision_id: UUID, actor: Actor
    ):
        return service.inspect_sample_revision(actor, namespace_id, sample_id, revision_id)

    @app.get(
        prefix + "/samples/{sample_id}/revisions/{revision_id}", response_model=c.SampleRevision
    )
    def get_sample_revision(namespace_id: UUID, sample_id: UUID, revision_id: UUID, actor: Actor):
        return service.get_sample_revision(actor, namespace_id, sample_id, revision_id)

    @app.put(
        prefix + "/datasets/{dataset_id}/draft/members/{sample_id}", response_model=c.DraftChange
    )
    def set_membership(
        namespace_id: UUID,
        dataset_id: UUID,
        sample_id: UUID,
        command: c.MembershipWrite,
        actor: Actor,
    ):
        return service.set_membership(actor, namespace_id, dataset_id, sample_id, command)

    @app.delete(
        prefix + "/datasets/{dataset_id}/draft/members/{sample_id}", response_model=c.DraftChange
    )
    def remove_membership(
        namespace_id: UUID,
        dataset_id: UUID,
        sample_id: UUID,
        actor: Actor,
        expected_revision: Annotated[int, Query(ge=0)],
    ):
        return service.remove_membership(
            actor, namespace_id, dataset_id, sample_id, expected_revision
        )

    @app.get(prefix + "/datasets/{dataset_id}/draft/members", response_model=c.DraftPage)
    def list_memberships(
        namespace_id: UUID,
        dataset_id: UUID,
        actor: Actor,
        revision: Annotated[int | None, Query(ge=0)] = None,
        after: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ):
        return service.list_memberships(
            actor, namespace_id, dataset_id, revision=revision, after=after, limit=limit
        )

    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        try:
            async with previous_lifespan(application):
                yield
        finally:
            if owns_storage and storage is not None:
                storage.close()

    app.router.lifespan_context = lifespan
    return app
