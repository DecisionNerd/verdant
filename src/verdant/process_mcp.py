"""MCP process/profile transport; all authority remains in shared services."""

from typing import Annotated
from uuid import UUID

from pydantic import Field

from verdant.model_profiles import ModelProfilePage, ModelProfileService, Profile, ProfileCreate
from verdant.operations import Operation, OperationPage
from verdant.process_flow import (
    Flow,
    FlowCreate,
    FlowPage,
    ProcessAction,
    ProcessDetail,
    ProcessService,
    ProcessStart,
)

PageLimit = Annotated[int, Field(ge=1, le=100)]


def install_process_tools(mcp, sessions, invoke):
    profiles, process = ModelProfileService(sessions), ProcessService(sessions)

    @mcp.tool()
    async def model_profile_create(namespace_id: UUID, command: ProfileCreate) -> Profile:
        return await invoke(profiles.create, namespace_id, command)

    @mcp.tool()
    async def model_profiles_list(
        namespace_id: UUID, after: UUID | None = None, limit: PageLimit = 50
    ) -> ModelProfilePage:
        return await invoke(profiles.list, namespace_id, after, limit)

    @mcp.tool()
    async def model_profile_get(namespace_id: UUID, profile_id: UUID) -> Profile:
        return await invoke(profiles.get, namespace_id, profile_id)

    @mcp.tool()
    async def process_flow_create(
        namespace_id: UUID, dataset_id: UUID, command: FlowCreate
    ) -> Flow:
        return await invoke(process.create_flow, namespace_id, dataset_id, command)

    @mcp.tool()
    async def process_flows_list(
        namespace_id: UUID, dataset_id: UUID, after: UUID | None = None, limit: PageLimit = 50
    ) -> FlowPage:
        return await invoke(process.list_flows, namespace_id, dataset_id, after, limit)

    @mcp.tool()
    async def process_flow_get(namespace_id: UUID, dataset_id: UUID, flow_id: UUID) -> Flow:
        return await invoke(process.get_flow, namespace_id, dataset_id, flow_id)

    @mcp.tool()
    async def process_runs_list(
        namespace_id: UUID, dataset_id: UUID, after: UUID | None = None, limit: PageLimit = 25
    ) -> OperationPage:
        return await invoke(process.list_runs, namespace_id, dataset_id, after, limit)

    @mcp.tool()
    async def process_start(
        namespace_id: UUID, dataset_id: UUID, command: ProcessStart
    ) -> Operation:
        return await invoke(process.start, namespace_id, dataset_id, command)

    @mcp.tool()
    async def process_get(
        namespace_id: UUID,
        operation_id: UUID,
        after_sample: UUID | None = None,
        limit: Annotated[int, Field(ge=1, le=25)] = 25,
    ) -> ProcessDetail:
        return await invoke(
            process.detail, namespace_id, operation_id, after_sample=after_sample, limit=limit
        )

    @mcp.tool()
    async def process_action(
        namespace_id: UUID, operation_id: UUID, command: ProcessAction
    ) -> Operation:
        return await invoke(process.action, namespace_id, operation_id, command)
