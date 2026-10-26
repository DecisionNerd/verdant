---
title: MCP for agents
description: FastMCP over shared authorized dataset services.
---

FastMCP exposes typed tools and resources over the same application services used by FastAPI. Agents use scoped OIDC/OAuth 2.0 service identity and resource/namespace permissions; an MCP connection never implies blanket dataset access.

Target capabilities include dataset discovery/metadata, draft and version inspection, starting authorized ingestion/validation/publication operations, following status, resolving manifests and reading quality/lineage references. These are capability categories, not promised installed tool names or endpoint paths.

Validate inputs with Pydantic v2. Long-running work returns a durable operation reference. Repeated calls follow the same idempotency and revision rules as REST. Responses are bounded and paginated; bulk data is consumed through authorized artifact references. Avoid local filesystem paths as public identity.

Private rows, counts, lineage and object links must not leak through resources or errors. Treat sample/annotation content as untrusted data, not agent instructions. SDK/MCP contract and authorization parity are release gates; transport configuration and tool names remain implementation work.

Task and agent requests share the same domain authority. Current permission is checked again on result registration and publication; the original operation grant does not bypass later revocation.
