# ADR-0006: Database-as-state (Turso/libSQL) + OpenObserve

- **Status:** Accepted
- **Date:** 2026-07-21
- **Deciders:** Verdant maintainers

## Context

Verdant stored operational state as JSON files under `data/jobs/*.json`, `data/config.json`,
and `outputs/*/review.json`. That worked for a single operator machine but made concurrent
progress updates fragile and mixed durable control-plane state with large blob outputs.

We also needed first-class ops observability (traces/logs) without requiring Langfuse for
every local run. Langfuse remains valuable for LLM evals/datasets.

## Options considered

### Option A — Keep filesystem JSON as source of truth
- **Pros:** Zero new dependencies; easy to inspect.
- **Cons:** Race-prone patches; hard to query; no shared transactional model.

### Option B — Embedded libSQL (Turso file URL) for operational state; blobs on disk
- **Pros:** SQLite durability + SQL queries; persists on `./data`; same bind mount for all
  services; Turso Cloud optional later.
- **Cons:** New dependency (`@libsql/client`); one-shot migration from JSON.

### Option C — Full Postgres / Turso Cloud only
- **Pros:** Multi-tenant ready.
- **Cons:** Heavier local Compose; overkill for single-operator default.

### Observability: OpenObserve vs Langfuse-only
- OpenObserve (single binary + volume) for OTLP traces/logs as primary Verdant telemetry.
- Langfuse stays optional for evals/LLM dataset UX.

## Decision

1. **Operational state** (jobs, job events, LLM settings, reviews) lives in an embedded
   **libSQL** database at `file:/data/turso/verdant.db` (`TURSO_DATABASE_URL`).
2. **Blobs** (inputs, `document.md`/`html`, page IR, artifacts) remain on the filesystem
   under `DATA_DIR`.
3. **OpenObserve** is added to Compose with a **named volume** (`openobserve_data`) on host
   ports **18706** (HTTP) / **18707** (gRPC). Verdant exports OTel traces via OTLP HTTP.
4. **Langfuse** remains an optional sidecar; Verdant still supports `LangfuseSpanProcessor`
   when keys are set (dual export).

## Consequences

- **Positive:** Durable, queryable job/settings state; OpenObserve UI for digests without
  Langfuse; clear split between state DB and blob store.
- **Negative:** Operators must keep `./data/turso` and the OpenObserve volume; first boot
  migrates legacy JSON once (`.migrated` / `.db-migrated` markers).
- **Follow-up:** Optional Turso Cloud remote URL; log pipelines into OpenObserve streams.
