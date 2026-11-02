"""Typed MCP annotation/truth commands over the API's exact shared service instances."""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field

from verdant import annotation_contracts as a

PageLimit = Annotated[int, Field(ge=1, le=100)]


def install_annotation_tools(mcp, services, invoke):
    annotations, truth = services

    async def call(model, method, *args, **kwargs):
        result = await invoke(method, *args, **kwargs)
        if isinstance(result, BaseModel):
            result = result.model_dump(mode="json", by_alias=True, exclude_unset=True)
        # The public schema is explicit, while omit-unset serialization preserves
        # exact persisted evidence payloads and their content digests.
        return model.model_validate(result).model_dump(
            mode="json", by_alias=True, exclude_unset=True
        )

    @mcp.tool(output_schema=a.IntegrationResponse.model_json_schema())
    async def annotation_integration(namespace_id: UUID) -> dict:
        return await call(a.IntegrationResponse, annotations.status, namespace_id)

    @mcp.tool(output_schema=a.Assignment.model_json_schema())
    async def annotation_assignment_create(
        namespace_id: UUID, dataset_id: UUID, command: a.AssignmentCreate
    ) -> dict:
        return await call(a.Assignment, annotations.create, namespace_id, dataset_id, command)

    @mcp.tool(output_schema=a.AssignmentPage.model_json_schema())
    async def annotation_assignments_list(
        namespace_id: UUID, dataset_id: UUID, after: UUID | None = None, limit: PageLimit = 25
    ) -> dict:
        return await call(
            a.AssignmentPage, annotations.list, namespace_id, dataset_id, after, limit
        )

    @mcp.tool(output_schema=a.AssignmentDetailResponse.model_json_schema())
    async def annotation_assignment_get(
        namespace_id: UUID, dataset_id: UUID, assignment_id: UUID
    ) -> dict:
        return await call(
            a.AssignmentDetailResponse,
            annotations.detail,
            namespace_id,
            dataset_id,
            assignment_id,
        )

    @mcp.tool(output_schema=a.AssignmentDetailResponse.model_json_schema())
    async def annotation_import(
        namespace_id: UUID, dataset_id: UUID, assignment_id: UUID, command: a.ImportRequest
    ) -> dict:
        return await call(
            a.AssignmentDetailResponse,
            annotations.import_native,
            namespace_id,
            dataset_id,
            assignment_id,
            command,
        )

    @mcp.tool(output_schema=a.ReportEvidence.model_json_schema())
    async def agreement_report_create(
        namespace_id: UUID, dataset_id: UUID, command: a.ReportCreate
    ) -> dict:
        return await call(a.ReportEvidence, annotations.report, namespace_id, dataset_id, command)

    @mcp.tool(output_schema=a.ReportEvidence.model_json_schema())
    async def agreement_report_get(namespace_id: UUID, dataset_id: UUID, report_id: UUID) -> dict:
        return await call(
            a.ReportEvidence, annotations.get_evidence, namespace_id, dataset_id, report_id
        )

    @mcp.tool(output_schema=a.CanonicalEvidence.model_json_schema())
    async def annotation_adjudicate(
        namespace_id: UUID, dataset_id: UUID, command: a.Adjudicate
    ) -> dict:
        return await call(
            a.CanonicalEvidence, annotations.adjudicate, namespace_id, dataset_id, command
        )

    @mcp.tool(output_schema=a.CanonicalEvidence.model_json_schema())
    async def canonical_annotation_get(
        namespace_id: UUID, dataset_id: UUID, canonical_id: UUID
    ) -> dict:
        return await call(
            a.CanonicalEvidence,
            annotations.get_evidence,
            namespace_id,
            dataset_id,
            canonical_id,
            canonical=True,
        )

    @mcp.tool(output_schema=a.TruthWorkspace.model_json_schema())
    async def truth_workspace(namespace_id: UUID, dataset_id: UUID) -> dict:
        return await call(a.TruthWorkspace, truth.workspace, namespace_id, dataset_id)

    @mcp.tool(output_schema=a.TruthPage.model_json_schema())
    async def truth_versions_list(
        namespace_id: UUID, dataset_id: UUID, after: UUID | None = None, limit: PageLimit = 25
    ) -> dict:
        return await call(a.TruthPage, truth.list, namespace_id, dataset_id, after, limit)

    @mcp.tool(output_schema=a.TruthEvidence.model_json_schema())
    async def truth_version_get(namespace_id: UUID, dataset_id: UUID, truth_id: UUID) -> dict:
        return await call(a.TruthEvidence, truth.get, namespace_id, dataset_id, truth_id)

    @mcp.tool(output_schema=a.TruthEvidence.model_json_schema())
    async def truth_publish(namespace_id: UUID, dataset_id: UUID, command: a.TruthPublish) -> dict:
        return await call(a.TruthEvidence, truth.publish, namespace_id, dataset_id, command)
