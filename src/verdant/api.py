"""FastAPI composition over shared services. Identity verification is supplied by #11."""

import os
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from verdant import contracts as c
from verdant import publication as p
from verdant import quality_service as q
from verdant.application import RegistryService
from verdant.auth import IdentityRuntime, IdentitySettings
from verdant.domain import Conflict, DomainError, Forbidden, NotFound, Principal
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
from verdant.publication_runner import PublicationRunner
from verdant.storage import S3Storage, StorageError, configured_storage
from verdant.versioning import (
    Candidate,
    CandidateCapture,
    CandidatePage,
    CandidatePrepare,
    CandidateSelection,
    GenerationPage,
    VersioningService,
)


def verified_principal() -> Principal:
    """Fail closed until the Keycloak verifier is installed; no header/password shortcut."""
    raise HTTPException(status_code=401, detail="Verified identity required")


Actor = Annotated[Principal, Depends(verified_principal)]


def create_app(
    sessions: sessionmaker[Session] | None = None,
    identity_settings: IdentitySettings | None = None,
    artifact_storage: S3Storage | None = None,
) -> FastAPI:
    configure_access_logging()
    sessions = sessions or make_sessions(os.environ["VERDANT_DATABASE_URL"])
    service = RegistryService(sessions)
    operations = OperationService(sessions)
    quality = q.QualityService(sessions)
    publications = p.PublicationService(
        sessions,
        bucket=artifact_storage.settings.bucket
        if artifact_storage
        else os.environ.get("VERDANT_S3_BUCKET", "verdant-artifacts"),
    )
    versioning = VersioningService(sessions)
    app = FastAPI(title="Verdant platform", version=str(c.CONTRACT_VERSION))
    identity_settings = identity_settings or IdentitySettings.from_env()
    if identity_settings is not None:
        IdentityRuntime(sessions, identity_settings).install(app, verified_principal)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        code = {Forbidden: 403, NotFound: 404, Conflict: 409}[type(exc)]
        return JSONResponse(status_code=code, content={"detail": str(exc)})

    @app.exception_handler(StorageError)
    async def storage_error(request: Request, exc: StorageError):
        return JSONResponse(
            status_code=503, content={"detail": "Required artifact storage is unavailable"}
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
    publication_prefix = prefix + "/datasets/{dataset_id}"

    @app.post(
        publication_prefix + "/candidates/{candidate_id}/publications",
        response_model=p.Publication,
        status_code=202,
    )
    def start_publication(
        namespace_id: UUID,
        dataset_id: UUID,
        candidate_id: UUID,
        command: p.PublicationStart,
        actor: Actor,
    ):
        return publications.start(actor, namespace_id, dataset_id, candidate_id, command)

    @app.get(
        publication_prefix + "/candidates/{candidate_id}/publications",
        response_model=p.PublicationPage,
    )
    def list_publications(
        namespace_id: UUID,
        dataset_id: UUID,
        candidate_id: UUID,
        actor: Actor,
        after: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ):
        return publications.list(actor, namespace_id, dataset_id, candidate_id, after, limit)

    @app.get(publication_prefix + "/publications/{publication_id}", response_model=p.Publication)
    def get_publication(namespace_id: UUID, dataset_id: UUID, publication_id: UUID, actor: Actor):
        return publications.get(actor, namespace_id, dataset_id, publication_id)

    @app.post(
        publication_prefix + "/publications/{publication_id}/prepare", response_model=p.Publication
    )
    def prepare_publication(
        namespace_id: UUID,
        dataset_id: UUID,
        publication_id: UUID,
        command: p.PublicationBatch,
        actor: Actor,
    ):
        return PublicationRunner(sessions, artifact_storage or configured_storage()).prepare(
            actor, namespace_id, dataset_id, publication_id, command
        )

    @app.post(
        publication_prefix + "/publications/{publication_id}/finalize", response_model=p.Publication
    )
    def finalize_publication(
        namespace_id: UUID,
        dataset_id: UUID,
        publication_id: UUID,
        command: p.EmptyCommand,
        actor: Actor,
    ):
        return publications.finalize(actor, namespace_id, dataset_id, publication_id)

    @app.post(
        publication_prefix + "/publications/{publication_id}/renew", response_model=p.Publication
    )
    def renew_publication(
        namespace_id: UUID,
        dataset_id: UUID,
        publication_id: UUID,
        command: p.EmptyCommand,
        actor: Actor,
    ):
        return publications.renew(actor, namespace_id, dataset_id, publication_id)

    @app.post(
        publication_prefix + "/publications/{publication_id}/release", response_model=p.Publication
    )
    def release_publication(
        namespace_id: UUID,
        dataset_id: UUID,
        publication_id: UUID,
        command: p.PublicationRelease,
        actor: Actor,
    ):
        return publications.release(actor, namespace_id, dataset_id, publication_id, command)

    quality_prefix = prefix + "/datasets/{dataset_id}/quality"

    @app.post(quality_prefix + "/policies", response_model=q.Policy, status_code=201)
    def create_quality_policy(
        namespace_id: UUID, dataset_id: UUID, command: q.PolicyCreate, actor: Actor
    ):
        return quality.create_policy(actor, namespace_id, dataset_id, command)

    @app.get(quality_prefix + "/policies", response_model=q.PolicyPage)
    def list_quality_policies(
        namespace_id: UUID,
        dataset_id: UUID,
        actor: Actor,
        after: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ):
        return quality.list_policies(actor, namespace_id, dataset_id, after, limit)

    @app.get(quality_prefix + "/policies/{policy_id}", response_model=q.Policy)
    def get_quality_policy(namespace_id: UUID, dataset_id: UUID, policy_id: UUID, actor: Actor):
        return quality.get_policy(actor, namespace_id, dataset_id, policy_id)

    report_prefix = prefix + "/datasets/{dataset_id}/candidates/{candidate_id}/quality/reports"

    @app.post(report_prefix, response_model=q.Report, status_code=202)
    def start_quality_validation(
        namespace_id: UUID,
        dataset_id: UUID,
        candidate_id: UUID,
        command: q.ValidationStart,
        actor: Actor,
    ):
        return quality.start(actor, namespace_id, dataset_id, candidate_id, command)

    @app.get(report_prefix, response_model=q.ReportPage)
    def list_quality_reports(
        namespace_id: UUID,
        dataset_id: UUID,
        candidate_id: UUID,
        actor: Actor,
        after: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ):
        return quality.list_reports(actor, namespace_id, dataset_id, candidate_id, after, limit)

    @app.get(quality_prefix + "/reports/{report_id}", response_model=q.Report)
    def get_quality_report(namespace_id: UUID, dataset_id: UUID, report_id: UUID, actor: Actor):
        return quality.get_report(actor, namespace_id, dataset_id, report_id)

    @app.post(quality_prefix + "/reports/{report_id}/evaluate", response_model=q.Report)
    def advance_quality_validation(
        namespace_id: UUID,
        dataset_id: UUID,
        report_id: UUID,
        command: q.ValidationBatch,
        actor: Actor,
    ):
        return quality.advance(actor, namespace_id, dataset_id, report_id, command)

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

    candidates = prefix + "/datasets/{dataset_id}/candidates"

    @app.post(candidates, response_model=Candidate, status_code=201)
    def capture_candidate(
        namespace_id: UUID, dataset_id: UUID, command: CandidateCapture, actor: Actor
    ):
        return versioning.capture(actor, namespace_id, dataset_id, command)

    @app.get(candidates, response_model=CandidatePage)
    def list_candidates(
        namespace_id: UUID,
        dataset_id: UUID,
        actor: Actor,
        after: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ):
        return versioning.list_candidates(actor, namespace_id, dataset_id, after, limit)

    @app.get(candidates + "/{candidate_id}", response_model=Candidate)
    def get_candidate(namespace_id: UUID, dataset_id: UUID, candidate_id: UUID, actor: Actor):
        return versioning.get(actor, namespace_id, dataset_id, candidate_id)

    @app.get(candidates + "/{candidate_id}/members", response_model=GenerationPage)
    def candidate_members(
        namespace_id: UUID,
        dataset_id: UUID,
        candidate_id: UUID,
        actor: Actor,
        after: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ):
        return versioning.members(actor, namespace_id, dataset_id, candidate_id, after, limit)

    @app.post(candidates + "/{candidate_id}/prepare", response_model=Candidate)
    def prepare_candidate(
        namespace_id: UUID,
        dataset_id: UUID,
        candidate_id: UUID,
        command: CandidatePrepare,
        actor: Actor,
    ):
        return versioning.prepare(actor, namespace_id, dataset_id, candidate_id, command)

    @app.post(candidates + "/{candidate_id}/check-selection", response_model=Candidate)
    def check_candidate_selection(
        namespace_id: UUID,
        dataset_id: UUID,
        candidate_id: UUID,
        command: CandidateSelection,
        actor: Actor,
    ):
        return versioning.check_selection(actor, namespace_id, dataset_id, candidate_id, command)

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

    return app
