"""FastAPI annotation/truth adapters over shared authorized services."""

import os
import ssl
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query
from fastapi.responses import JSONResponse

from verdant import annotation_contracts as a
from verdant.annotations import AnnotationService
from verdant.domain import Principal
from verdant.label_studio import (
    LabelStudioClient,
    LabelStudioConflict,
    LabelStudioSettings,
    LabelStudioUnavailable,
)
from verdant.truth import TruthService


def configured_client():
    fields = [
        os.environ.get("VERDANT_LS_" + name)
        for name in ("BROWSER_ORIGIN", "MACHINE_ORIGIN", "IMAGE", "TOKEN")
    ]
    if not any(fields):
        return None
    if not all(fields):
        raise ValueError(
            "Configure all VERDANT_LS_BROWSER_ORIGIN/MACHINE_ORIGIN/IMAGE/TOKEN values"
        )
    return LabelStudioClient(
        LabelStudioSettings(*fields[:3]),
        fields[3],
        verify=ssl.create_default_context(cafile=os.environ.get("VERDANT_LS_CA_BUNDLE")),
    )


def install_annotation_routes(app, sessions, verified_principal, *, client=None):
    owns_client = client is None
    client = client or configured_client()
    annotations = AnnotationService(sessions, client)
    truth = TruthService(sessions)
    Actor = Annotated[Principal, Depends(verified_principal)]
    base = "/api/v1/namespaces/{namespace_id}"
    dataset = base + "/datasets/{dataset_id}"

    @app.exception_handler(LabelStudioUnavailable)
    async def unavailable(request, error):
        return JSONResponse(
            status_code=503, content={"detail": str(error)}, headers={"Cache-Control": "no-store"}
        )

    @app.exception_handler(LabelStudioConflict)
    async def native_conflict(request, error):
        return JSONResponse(
            status_code=409, content={"detail": str(error)}, headers={"Cache-Control": "no-store"}
        )

    @app.get(
        base + "/annotation-integration",
        response_model_exclude_unset=True,
        response_model=a.IntegrationResponse,
    )
    def integration(namespace_id: UUID, actor: Actor):
        return annotations.status(actor, namespace_id)

    @app.get(
        dataset + "/annotations", response_model_exclude_unset=True, response_model=a.AssignmentPage
    )
    def assignments(
        namespace_id: UUID,
        dataset_id: UUID,
        actor: Actor,
        after: UUID | None = None,
        limit: int = Query(25, ge=1, le=100),
    ):
        return annotations.list(actor, namespace_id, dataset_id, after, limit)

    @app.post(
        dataset + "/annotations", response_model_exclude_unset=True, response_model=a.Assignment
    )
    def create_assignment(
        namespace_id: UUID, dataset_id: UUID, actor: Actor, command: a.AssignmentCreate
    ):
        return annotations.create(actor, namespace_id, dataset_id, command)

    @app.get(
        dataset + "/annotations/{assignment_id}",
        response_model_exclude_unset=True,
        response_model=a.AssignmentDetailResponse,
    )
    def assignment(namespace_id: UUID, dataset_id: UUID, assignment_id: UUID, actor: Actor):
        return annotations.detail(actor, namespace_id, dataset_id, assignment_id)

    @app.post(
        dataset + "/annotations/{assignment_id}/import",
        response_model_exclude_unset=True,
        response_model=a.AssignmentDetailResponse,
    )
    def import_annotations(
        namespace_id: UUID,
        dataset_id: UUID,
        assignment_id: UUID,
        actor: Actor,
        command: a.ImportRequest,
    ):
        return annotations.import_native(actor, namespace_id, dataset_id, assignment_id, command)

    @app.post(
        dataset + "/agreement", response_model_exclude_unset=True, response_model=a.ReportEvidence
    )
    def agreement(namespace_id: UUID, dataset_id: UUID, actor: Actor, command: a.ReportCreate):
        return annotations.report(actor, namespace_id, dataset_id, command)

    @app.get(
        dataset + "/agreement/{report_id}",
        response_model_exclude_unset=True,
        response_model=a.ReportEvidence,
    )
    def report(namespace_id: UUID, dataset_id: UUID, report_id: UUID, actor: Actor):
        return annotations.get_evidence(actor, namespace_id, dataset_id, report_id)

    @app.post(
        dataset + "/adjudications",
        response_model_exclude_unset=True,
        response_model=a.CanonicalEvidence,
    )
    def adjudicate(namespace_id: UUID, dataset_id: UUID, actor: Actor, command: a.Adjudicate):
        return annotations.adjudicate(actor, namespace_id, dataset_id, command)

    @app.get(
        dataset + "/adjudications/{canonical_id}",
        response_model_exclude_unset=True,
        response_model=a.CanonicalEvidence,
    )
    def canonical(namespace_id: UUID, dataset_id: UUID, canonical_id: UUID, actor: Actor):
        return annotations.get_evidence(
            actor, namespace_id, dataset_id, canonical_id, canonical=True
        )

    @app.get(
        dataset + "/truth-workspace",
        response_model_exclude_unset=True,
        response_model=a.TruthWorkspace,
    )
    def truth_workspace(namespace_id: UUID, dataset_id: UUID, actor: Actor):
        return truth.workspace(actor, namespace_id, dataset_id)

    @app.get(dataset + "/truth", response_model_exclude_unset=True, response_model=a.TruthPage)
    def truths(
        namespace_id: UUID,
        dataset_id: UUID,
        actor: Actor,
        after: UUID | None = None,
        limit: int = Query(25, ge=1, le=100),
    ):
        return truth.list(actor, namespace_id, dataset_id, after, limit)

    @app.get(
        dataset + "/truth/{truth_id}",
        response_model_exclude_unset=True,
        response_model=a.TruthEvidence,
    )
    def truth_version(namespace_id: UUID, dataset_id: UUID, truth_id: UUID, actor: Actor):
        return truth.get(actor, namespace_id, dataset_id, truth_id)

    @app.post(dataset + "/truth", response_model_exclude_unset=True, response_model=a.TruthEvidence)
    def publish_truth(namespace_id: UUID, dataset_id: UUID, actor: Actor, command: a.TruthPublish):
        return truth.publish(actor, namespace_id, dataset_id, command)

    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        try:
            async with previous_lifespan(application):
                yield
        finally:
            if owns_client and client is not None:
                client.close()

    app.router.lifespan_context = lifespan
    return annotations, truth
