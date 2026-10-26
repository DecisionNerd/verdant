# ADR-0006: Recoverable reads, lifecycle and shared-resource budgets

- **Status:** Accepted (target architecture)
- **Date:** 2026-10-25
- **Decider:** Repository owner

## Context

Interactive use and dataset consumption must remain usable during background load, stale projections and optional component outages.

## Decision

Start with indexed authoritative queries; add domain-owned projections only for measured query needs. Checkpoint/inbox effects atomically, detect stale aggregate revisions, and rebuild resumably from authoritative state plus retained events at a consistent watermark. Switch readers after catch-up; authoritative permissions/publication/exact-version reads never depend on stale projections. Distinguish durable internal consumers, reconnectable SSE with status fallback, and opt-in outbound webhooks with bounded independent delivery.

Deprecation warns and changes recommendations; archive hides default discovery but preserves authorized exact-version reads and bytes. Archived datasets pause new work/publication while preserving admitted-work checkpoints. Unarchive restores admission/visibility. Deletion is a separate explicit retention/authorization operation.

Bound API and background connection pools, worker concurrency, in-flight buffers and batches with total database headroom. Use short transactions, jittered retries, backpressure and durable resume. Optional integration/projection/telemetry failures degrade dependent features only. Required gates, unavailable content and invalid identity cannot report false success.

## Consequences and verification

No new service or fixed pool/corpus size is mandated. Qualify modality-neutral workloads and measure interactive responsiveness under ingestion/rebuild/retry saturation. Prove coordinated database/artifact restore and retention integrity before capacity or fault-tolerance claims.

See [Architecture](../ARCHITECTURE.md), [Implementation](../IMPLEMENTATION.md) and [Testing](../TESTING.md) for the complete contract and proof plan.
