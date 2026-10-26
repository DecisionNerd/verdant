# ADR-0003: Immutable snapshots and bounded publication

- **Status:** Accepted (target architecture)
- **Date:** 2026-10-25
- **Decider:** Repository owner

## Context

Publication must preserve exact interpretation and bytes, tolerate concurrent editing and avoid large finalization transactions.

## Decision

PostgreSQL owns registry, revisions, operations, lineage, quality metadata, audit and durable events. S3-compatible storage owns large data/manifests/reports. Freeze sample metadata/bytes, annotation/task/label-schema revisions, review evidence, splits, transformation definitions/code/configuration/environment and publication policy/gate evidence. Catalog availability/access policy remains current. Capture a stable draft revision, prepare and seal a membership generation in resumable batches, and validate that candidate. Default publication targets this frozen candidate while editing continues; only an explicit current-draft action requires latest-revision equality. Use deterministic streaming manifests; shard only when measured needs justify it.

Preparation establishes a complete durable retention group and verification evidence tied to generation/manifest/gates. Finalization uses candidate/group and operation guards to atomically promote group protection, create the unique dataset/version reference, and commit audit/event/outbox/result. It performs no membership/artifact scan, reference rewrite or external call. Expiry/release uses the same group guard; expired preparation must regain protection/readiness. Artifact deletion claims serialize with reference creation and cannot collect protected objects. Published protection never expires implicitly.

## Consequences and verification

Candidate, generation, review evidence, effect receipts and artifact protection are logical concepts, not mandated table/service counts. Verify snapshot capture, retention races, idempotent publication and bounded finalization with real PostgreSQL/object storage. Out-of-band corruption needs detection and repair/restore. Preserve readers for issued schema versions.

See [Architecture](../ARCHITECTURE.md), [Implementation](../IMPLEMENTATION.md) and [Testing](../TESTING.md) for the complete contract and proof plan.
