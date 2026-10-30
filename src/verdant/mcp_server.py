"""FastMCP transport over the same current-authorization registry use cases."""

from typing import Annotated
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from verdant import quality_service as q
from verdant.access_tokens import InvalidAccessToken
from verdant.application import RegistryService
from verdant.contracts import SAMPLE_INGESTION_KINDS, DatasetCreate, DatasetUpdate
from verdant.domain import DomainError, Forbidden, Principal
from verdant.ingestion import (
    IngestionService,
    ReuploadRequest,
    SourceRegistrationRequest,
    UploadPartRequest,
    UploadRequest,
)
from verdant.operations import OperationService, VerificationRequest
from verdant.preview import PreviewService
from verdant.storage import StorageError
from verdant.versioning import (
    CandidateCapture,
    CandidatePrepare,
    CandidateSelection,
    VersioningService,
)

OperationLimit = Annotated[int, Field(ge=1, le=100)]
OperationEpoch = Annotated[int, Field(ge=1, le=9223372036854775807, strict=True)]


class RegistryTokenVerifier(TokenVerifier):
    def __init__(self, runtime):
        super().__init__(required_scopes=["verdant"])
        self.runtime = runtime

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = await self.runtime.tokens.verify_async(token)
            actor = await run_in_threadpool(
                self.runtime.identities.resolve,
                claims["iss"],
                claims["sub"],
                kind="service",
            )
        except (InvalidAccessToken, Forbidden):
            return None
        return AccessToken(
            token=token,
            client_id=claims["azp"],
            scopes=claims["scope"].split(),
            expires_at=claims["exp"],
            subject=claims["sub"],
            claims=claims | {"verdant_principal_id": str(actor.id)},
        )


def create_mcp_app(runtime, *, storage=None):
    mcp = FastMCP("Verdant", auth=RegistryTokenVerifier(runtime))
    service = RegistryService(runtime.sessions)
    operations = OperationService(runtime.sessions)
    previews = PreviewService(runtime.sessions, storage)
    ingestion = IngestionService(
        runtime.sessions,
        bucket=storage.settings.bucket if storage else "unconfigured",
        storage=storage,
    )
    versioning = VersioningService(runtime.sessions)
    quality = q.QualityService(runtime.sessions)

    def actor() -> Principal:
        token = get_access_token()
        if token is None:
            raise ToolError("Verified service identity required")
        return Principal(UUID(token.claims["verdant_principal_id"]), transport="mcp")

    async def invoke(method, *args, **kwargs):
        try:
            return await run_in_threadpool(method, actor(), *args, **kwargs)
        except (DomainError, StorageError) as exc:
            raise ToolError(str(exc)) from None

    @mcp.tool()
    async def quality_policy_create(namespace_id: UUID, dataset_id: UUID, command: q.PolicyCreate):
        return await invoke(quality.create_policy, namespace_id, dataset_id, command)

    @mcp.tool()
    async def quality_policies_list(
        namespace_id: UUID, dataset_id: UUID, after: UUID | None = None, limit: OperationLimit = 25
    ):
        return await invoke(quality.list_policies, namespace_id, dataset_id, after, limit)

    @mcp.tool()
    async def quality_policy_get(namespace_id: UUID, dataset_id: UUID, policy_id: UUID):
        return await invoke(quality.get_policy, namespace_id, dataset_id, policy_id)

    @mcp.tool()
    async def quality_validation_start(
        namespace_id: UUID, dataset_id: UUID, candidate_id: UUID, command: q.ValidationStart
    ):
        return await invoke(quality.start, namespace_id, dataset_id, candidate_id, command)

    @mcp.tool()
    async def quality_reports_list(
        namespace_id: UUID,
        dataset_id: UUID,
        candidate_id: UUID,
        after: UUID | None = None,
        limit: OperationLimit = 25,
    ):
        return await invoke(
            quality.list_reports, namespace_id, dataset_id, candidate_id, after, limit
        )

    @mcp.tool()
    async def quality_report_get(namespace_id: UUID, dataset_id: UUID, report_id: UUID):
        return await invoke(quality.get_report, namespace_id, dataset_id, report_id)

    @mcp.tool()
    async def quality_validation_advance(
        namespace_id: UUID, dataset_id: UUID, report_id: UUID, command: q.ValidationBatch
    ):
        return await invoke(quality.advance, namespace_id, dataset_id, report_id, command)

    @mcp.tool()
    async def sample_revision_preview(namespace_id: UUID, sample_id: UUID, revision_id: UUID):
        return await invoke(previews.get, namespace_id, sample_id, revision_id)

    @mcp.tool()
    async def ingestion_limits(namespace_id: UUID):
        return await invoke(ingestion.limits, namespace_id)

    @mcp.tool()
    async def ingestions_list(
        namespace_id: UUID, after: UUID | None = None, limit: OperationLimit = 25
    ):
        return await invoke(
            operations.list_operations, namespace_id, after, limit, kind=SAMPLE_INGESTION_KINDS
        )

    @mcp.tool()
    async def ingestion_upload_admit(namespace_id: UUID, command: UploadRequest):
        await invoke(ingestion.require_storage, namespace_id)
        return await invoke(ingestion.admit, namespace_id, command)

    @mcp.tool()
    async def ingestion_source_admit(namespace_id: UUID, command: SourceRegistrationRequest):
        return await invoke(ingestion.admit_source, namespace_id, command)

    @mcp.tool()
    async def ingestion_reupload(namespace_id: UUID, command: ReuploadRequest):
        await invoke(ingestion.require_storage, namespace_id)
        return await invoke(ingestion.reupload, namespace_id, command)

    @mcp.tool()
    async def ingestion_get(namespace_id: UUID, operation_id: UUID):
        return await invoke(ingestion.get, namespace_id, operation_id)

    @mcp.tool()
    async def ingestion_upload_initiate(
        namespace_id: UUID, operation_id: UUID, item_id: UUID, epoch: OperationEpoch
    ):
        return await invoke(ingestion.initiate_upload, namespace_id, operation_id, item_id, epoch)

    @mcp.tool()
    async def ingestion_upload_sign_part(
        namespace_id: UUID, operation_id: UUID, item_id: UUID, command: UploadPartRequest
    ):
        return await invoke(
            ingestion.sign_upload_part,
            namespace_id,
            operation_id,
            item_id,
            command.epoch,
            command.part_number,
        )

    @mcp.tool()
    async def ingestion_upload_parts(
        namespace_id: UUID, operation_id: UUID, item_id: UUID, epoch: OperationEpoch
    ):
        return await invoke(ingestion.upload_progress, namespace_id, operation_id, item_id, epoch)

    @mcp.tool()
    async def ingestion_upload_complete(
        namespace_id: UUID, operation_id: UUID, item_id: UUID, epoch: OperationEpoch
    ):
        return await invoke(ingestion.complete_upload, namespace_id, operation_id, item_id, epoch)

    @mcp.tool()
    async def ingestion_cancel(namespace_id: UUID, operation_id: UUID, epoch: OperationEpoch):
        return await invoke(ingestion.request_cancel, namespace_id, operation_id, epoch)

    @mcp.tool()
    async def candidate_capture(namespace_id: UUID, dataset_id: UUID, command: CandidateCapture):
        return await invoke(versioning.capture, namespace_id, dataset_id, command)

    @mcp.tool()
    async def candidates_list(
        namespace_id: UUID,
        dataset_id: UUID,
        after: UUID | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 25,
    ):
        return await invoke(versioning.list_candidates, namespace_id, dataset_id, after, limit)

    @mcp.tool()
    async def candidate_get(namespace_id: UUID, dataset_id: UUID, candidate_id: UUID):
        return await invoke(versioning.get, namespace_id, dataset_id, candidate_id)

    @mcp.tool()
    async def candidate_members(
        namespace_id: UUID,
        dataset_id: UUID,
        candidate_id: UUID,
        after: UUID | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 25,
    ):
        return await invoke(
            versioning.members, namespace_id, dataset_id, candidate_id, after, limit
        )

    @mcp.tool()
    async def candidate_prepare(
        namespace_id: UUID, dataset_id: UUID, candidate_id: UUID, command: CandidatePrepare
    ):
        return await invoke(versioning.prepare, namespace_id, dataset_id, candidate_id, command)

    @mcp.tool()
    async def candidate_check_selection(
        namespace_id: UUID, dataset_id: UUID, candidate_id: UUID, command: CandidateSelection
    ):
        return await invoke(
            versioning.check_selection, namespace_id, dataset_id, candidate_id, command
        )

    @mcp.tool()
    async def operation_verify_artifact(namespace_id: UUID, command: VerificationRequest):
        return await invoke(operations.admit_verification, namespace_id, command)

    @mcp.tool()
    async def operations_list(
        namespace_id: UUID, after: UUID | None = None, limit: OperationLimit = 25
    ):
        return await invoke(operations.list_operations, namespace_id, after, limit)

    @mcp.tool()
    async def operation_get(
        namespace_id: UUID,
        operation_id: UUID,
        after: UUID | None = None,
        limit: OperationLimit = 25,
    ):
        return await invoke(operations.detail, namespace_id, operation_id, after, limit)

    @mcp.tool()
    async def operation_retry(namespace_id: UUID, operation_id: UUID, epoch: OperationEpoch):
        return await invoke(operations.retry, namespace_id, operation_id, epoch)

    @mcp.tool()
    async def operation_cancel(namespace_id: UUID, operation_id: UUID, epoch: OperationEpoch):
        return await invoke(operations.request_cancel, namespace_id, operation_id, epoch)

    @mcp.tool()
    async def operation_reconcile(namespace_id: UUID, operation_id: UUID, epoch: OperationEpoch):
        return await invoke(operations.request_reconcile, namespace_id, operation_id, epoch)

    @mcp.tool()
    async def sample_revision_inspect(namespace_id: UUID, sample_id: UUID, revision_id: UUID):
        return (
            await invoke(service.inspect_sample_revision, namespace_id, sample_id, revision_id)
        ).model_dump(mode="json")

    @mcp.tool()
    async def samples_list(namespace_id: UUID, after: UUID | None = None, limit: int = 50):
        return (await invoke(service.list_samples, namespace_id, after, limit)).model_dump(
            mode="json"
        )

    @mcp.tool()
    async def namespaces_list(after: UUID | None = None, limit: int = 50):
        return (await invoke(service.list_namespaces, after, limit)).model_dump(mode="json")

    @mcp.tool()
    async def datasets_list(
        namespace_id: UUID,
        search: str = "",
        modality: str | None = None,
        owner_id: UUID | None = None,
        after: UUID | None = None,
        limit: int = 50,
    ):
        return (
            await invoke(
                service.list_datasets, namespace_id, search, modality, owner_id, after, limit
            )
        ).model_dump(mode="json")

    @mcp.tool()
    async def dataset_update(namespace_id: UUID, dataset_id: UUID, command: DatasetUpdate):
        return (await invoke(service.update_dataset, namespace_id, dataset_id, command)).model_dump(
            mode="json"
        )

    @mcp.tool()
    async def dataset_get(namespace_id: UUID, dataset_id: UUID):
        """Read a dataset in a currently authorized namespace."""
        return await invoke(service.get_dataset, namespace_id, dataset_id)

    @mcp.tool()
    async def dataset_create(namespace_id: UUID, command: DatasetCreate):
        """Create a dataset and revision-zero draft in an authorized namespace."""
        return await invoke(service.create_dataset, namespace_id, command)

    return mcp.http_app(path="/", stateless_http=True, json_response=True)
