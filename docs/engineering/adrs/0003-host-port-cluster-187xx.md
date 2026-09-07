# ADR-0003: Host port cluster in 187xx

- **Status:** Accepted
- **Date:** 2026-07-19
- **Deciders:** Verdant maintainers

## Context

Local developers often already run apps on 3000, 4321, 5432, 8000, etc. Verdant brings Web,
Docs, MCP, Langfuse, Trigger, and MinIO — colliding ports make first-run fail (NFR-2, FR-1).

## Options considered

### Option A — Framework defaults (3000, 4321, …)
- **Pros:** Familiar to each tool.
- **Cons:** Frequent collisions; hard to remember which Verdant service is which.

### Option B — Cluster defaults in 187xx, overridable via `.env`
- **Pros:** Memorable band; low collision; documented table.
- **Cons:** Non-standard ports surprise newcomers until they read the table.

### Option C — Random high ports each boot
- **Pros:** Avoids collisions.
- **Cons:** Breaks bookmarks, MCP configs, and docs.

## Decision

We will default host ports to the **187xx** cluster (`18700` web … `18707` OpenObserve gRPC),
documented in README, `.env.example`, and Starlight, with env overrides for each service.

| Port | Service |
|---|---|
| 18700 | Web UI |
| 18701 | Docs (Starlight) |
| 18702 | MCP |
| 18703 | Langfuse |
| 18704 | Trigger.dev |
| 18705 | Langfuse MinIO |
| 18706 | OpenObserve HTTP/UI |
| 18707 | OpenObserve gRPC |

## Consequences

- **Positive:** Predictable local URLs; MCP/docs links stay stable.
- **Negative:** Docs and Cursor MCP configs must use 187xx, not tool defaults.
- **Follow-up:** Keep Starlight and README tables in sync when ports change.
