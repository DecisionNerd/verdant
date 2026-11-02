"""HTTP contracts over shared process services; identity is supplied by API composition."""

from uuid import UUID

from fastapi import Query

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


def install_process_routes(app, sessions, actor_type):
    profiles, process = ModelProfileService(sessions), ProcessService(sessions)
    prefix = "/api/v1/namespaces/{namespace_id}"
    dataset = prefix + "/datasets/{dataset_id}"

    @app.post(prefix + "/model-profiles", response_model=Profile, status_code=201)
    def create_model_profile(namespace_id: UUID, command: ProfileCreate, actor: actor_type):
        return profiles.create(actor, namespace_id, command)

    @app.get(prefix + "/model-profiles", response_model=ModelProfilePage)
    def list_model_profiles(
        namespace_id: UUID,
        actor: actor_type,
        after: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
    ):
        return profiles.list(actor, namespace_id, after, limit)

    @app.get(prefix + "/model-profiles/{profile_id}", response_model=Profile)
    def get_model_profile(namespace_id: UUID, profile_id: UUID, actor: actor_type):
        return profiles.get(actor, namespace_id, profile_id)

    @app.post(dataset + "/process-flows", response_model=Flow, status_code=201)
    def create_process_flow(
        namespace_id: UUID, dataset_id: UUID, command: FlowCreate, actor: actor_type
    ):
        return process.create_flow(actor, namespace_id, dataset_id, command)

    @app.get(dataset + "/process-flows", response_model=FlowPage)
    def list_process_flows(
        namespace_id: UUID,
        dataset_id: UUID,
        actor: actor_type,
        after: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
    ):
        return process.list_flows(actor, namespace_id, dataset_id, after, limit)

    @app.get(dataset + "/process-flows/{flow_id}", response_model=Flow)
    def get_process_flow(namespace_id: UUID, dataset_id: UUID, flow_id: UUID, actor: actor_type):
        return process.get_flow(actor, namespace_id, dataset_id, flow_id)

    @app.get(dataset + "/process-runs", response_model=OperationPage)
    def list_process_runs(
        namespace_id: UUID,
        dataset_id: UUID,
        actor: actor_type,
        after: UUID | None = None,
        limit: int = Query(25, ge=1, le=100),
    ):
        return process.list_runs(actor, namespace_id, dataset_id, after, limit)

    @app.post(dataset + "/process-runs", response_model=Operation, status_code=202)
    def start_process(
        namespace_id: UUID, dataset_id: UUID, command: ProcessStart, actor: actor_type
    ):
        return process.start(actor, namespace_id, dataset_id, command)

    @app.get(prefix + "/process-runs/{operation_id}", response_model=ProcessDetail)
    def process_detail(
        namespace_id: UUID,
        operation_id: UUID,
        actor: actor_type,
        after_sample: UUID | None = None,
        limit: int = Query(25, ge=1, le=25),
    ):
        return process.detail(
            actor, namespace_id, operation_id, after_sample=after_sample, limit=limit
        )

    @app.post(prefix + "/process-runs/{operation_id}/actions", response_model=Operation)
    def process_action(
        namespace_id: UUID, operation_id: UUID, command: ProcessAction, actor: actor_type
    ):
        return process.action(actor, namespace_id, operation_id, command)
