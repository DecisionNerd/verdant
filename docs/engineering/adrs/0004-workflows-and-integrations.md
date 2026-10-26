# ADR-0004: Durable operations and retry-safe integrations

- **Status:** Accepted (target architecture)
- **Date:** 2026-10-25
- **Decider:** Repository owner

## Context

Workflow crashes and at-least-once delivery must recover without repeating committed logical effects or allowing stale workers to finalize newer work.

## Decision

Commit state, audit/event and outbox atomically in PostgreSQL; use transactional inbox deduplication for local effects. Operations owns logical status, attempt epochs, cancellation and final result acceptance. Prefect owns bounded task retries and long-running orchestration, not dataset authority. Independent domain adapters handle Label Studio annotation/review, MLflow associations and Evidently reports/gates without requiring a flow for every interaction. DataChain processes bulk artifacts directly.

Persist dispatch identity per operation attempt and reconcile uncertain submissions before replay. Logical effect identity excludes attempt epoch/retry count; durable request digests, unique receipt claims and provider references survive new attempts. Reuse completed effects, reconcile unknown ones, and create a new logical action only for deliberate repetition or changed inputs. Epochs fence workers/result acceptance rather than changing effect identity. Unknown non-idempotent provider writes stop for reconciliation; unrelated work continues. Cancellation/finalization serialize on the operation guard; prior committed effects remain auditable.

## Consequences and verification

No exactly-once transport or global ordering is promised. Qualify provider deduplication/lookup behavior, retain receipts through supported recovery windows and test failures after provider success but before result registration. External waits never hold database connections or transactions.

See [Architecture](../ARCHITECTURE.md), [Implementation](../IMPLEMENTATION.md) and [Testing](../TESTING.md) for the complete contract and proof plan.
