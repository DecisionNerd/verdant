"""FastMCP transport over the same current-authorization registry use cases."""

from uuid import UUID

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from starlette.concurrency import run_in_threadpool

from verdant.access_tokens import InvalidAccessToken
from verdant.application import RegistryService
from verdant.contracts import DatasetCreate, DatasetUpdate
from verdant.domain import DomainError, Forbidden, Principal


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


def create_mcp_app(runtime):
    mcp = FastMCP("Verdant", auth=RegistryTokenVerifier(runtime))
    service = RegistryService(runtime.sessions)

    def actor() -> Principal:
        token = get_access_token()
        if token is None:
            raise ToolError("Verified service identity required")
        return Principal(UUID(token.claims["verdant_principal_id"]), transport="mcp")

    async def invoke(method, *args):
        try:
            return await run_in_threadpool(method, actor(), *args)
        except DomainError as exc:
            raise ToolError(str(exc)) from None

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
