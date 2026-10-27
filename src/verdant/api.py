"""FastAPI composition over shared services. Identity verification is supplied by #11."""

import os
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from verdant import contracts as c
from verdant.application import RegistryService
from verdant.auth import IdentityRuntime, IdentitySettings
from verdant.domain import Conflict, DomainError, Forbidden, NotFound, Principal
from verdant.logging import configure_access_logging
from verdant.persistence import make_sessions


def verified_principal() -> Principal:
    """Fail closed until the Keycloak verifier is installed; no header/password shortcut."""
    raise HTTPException(status_code=401, detail="Verified identity required")


Actor = Annotated[Principal, Depends(verified_principal)]


def create_app(
    sessions: sessionmaker[Session] | None = None, identity_settings: IdentitySettings | None = None
) -> FastAPI:
    configure_access_logging()
    sessions = sessions or make_sessions(os.environ["VERDANT_DATABASE_URL"])
    service = RegistryService(sessions)
    app = FastAPI(title="Verdant platform", version=str(c.CONTRACT_VERSION))
    identity_settings = identity_settings or IdentitySettings.from_env()
    if identity_settings is not None:
        IdentityRuntime(sessions, identity_settings).install(app, verified_principal)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        code = {Forbidden: 403, NotFound: 404, Conflict: 409}[type(exc)]
        return JSONResponse(status_code=code, content={"detail": str(exc)})

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

    prefix = "/api/v1/namespaces/{namespace_id}"

    @app.post(prefix + "/datasets", response_model=c.Dataset, status_code=201)
    def create_dataset(namespace_id: UUID, command: c.DatasetCreate, actor: Actor):
        return service.create_dataset(actor, namespace_id, command)

    @app.get(prefix + "/datasets/{dataset_id}", response_model=c.Dataset)
    def get_dataset(namespace_id: UUID, dataset_id: UUID, actor: Actor):
        return service.get_dataset(actor, namespace_id, dataset_id)

    @app.post(prefix + "/samples", response_model=c.SampleRevision, status_code=201)
    def create_sample(namespace_id: UUID, command: c.SampleCreate, actor: Actor):
        return service.create_sample(actor, namespace_id, command)

    @app.post(
        prefix + "/samples/{sample_id}/revisions", response_model=c.SampleRevision, status_code=201
    )
    def revise_sample(namespace_id: UUID, sample_id: UUID, command: c.SampleRevise, actor: Actor):
        return service.revise_sample(actor, namespace_id, sample_id, command)

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
