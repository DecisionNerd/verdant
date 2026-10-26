# Experience design contract

The target surfaces are React/TypeScript/Vite, the Python SDK, REST clients, FastMCP agents, and Starlight documentation. [Architecture](engineering/ARCHITECTURE.md) governs shared semantics; this file governs how users encounter them. Existing UI components are not prescribed as the new design.

## Information architecture

Start at dataset discovery. A dataset workspace exposes overview/ownership, drafts, published versions, samples and annotations, quality, lineage, experiments, and operations. Select an exact version before displaying reproducible content or evaluation results. Show mutable drafts separately from immutable snapshots.

Use TanStack Query for server-state caching, TanStack Table for paginated/filterable collections, and React Flow for lineage. PostgreSQL owns authoritative state; shared domain services enforce business rules and permissions. FastAPI and FastMCP are transport adapters. A React graph or cached table is a projection, not a competing registry.

## Vocabulary and states

Use dataset, draft, published version, sample revision, annotation revision, manifest, quality policy/report, transformation, and operation consistently. Distinguish Prefect flow runs and MLflow experiment runs by name. Reserve “digest” for a document-processing transformation.

Show operation ID, current phase, progress where measurable, last update, and actionable error. Cancellation remains requested until Operations records the terminal outcome based on execution observations and committed effects. Retrying reports the attempt and preserves lineage. Disconnections and stale caches must not appear as successful completion.

Publication presents the selected candidate and its captured draft revision, pinned schema and quality policy/report, split counts, and manifest digest. Explain validation failures before allowing retry. A published version has no edit-in-place action; offer a new draft/version.

## Identity and access

Use the backend-owned Keycloak session for frontend login. Display active organizational project/namespace and principal context. Enabled dashboards open through the same Keycloak SSO session as the correct individual, without a second password; expose missing edition/plugin prerequisites as unavailable rather than claiming readiness. Disabled actions may explain permissions, but server-side authorization remains mandatory. Never expose hidden datasets through search counts, lineage, error details, or signed object links. Human and agent operations have the same scopes and audit identity.

## Accessibility

Provide keyboard navigation, visible focus, labeled form errors, accessible dialogs with focus restoration, text equivalents for lineage, non-color status indicators, and reduced-motion support. Body text meets WCAG AA contrast. Announce meaningful asynchronous status changes without flooding screen readers with every event.

## Interface parity

REST, SDK, and MCP return typed identifiers and operation references. Large collections use pagination; artifact access uses authorized references rather than server-local paths. Empty, loading, partial, failed, denied, and complete states remain distinct. CLI documentation describes the SDK/client workflow until a new CLI contract is implemented.

Archive/deprecation controls explain their effect on discovery and new work, preserve authorized pinned reads, and offer unarchive. Deletion is a separate explicit operation. Show projection freshness and degraded integration status without blocking unrelated work; reconnect progress through durable operation state. Avoid forcing users to launch a workflow for a lightweight metadata/integration action.

A candidate remains valid when later draft edits occur. Show candidate/source revision and latest draft revision together, preserving completed validation. Default publication targets the selected candidate; distinguish an explicit current-draft action and return a useful conflict without discarding preparation.
