# ADR-0008: Local S3 storage and cloud portability

- **Status:** Accepted (target architecture)
- **Date:** 2026-10-26
- **Decider:** Repository owner

## Context

Local dataset and observability clients need a compact shared object service that can be replaced by qualified cloud storage without introducing provider-specific domain identities or duplicate storage servers.

## Decision

Use SeaweedFS OSS single-node S3 (`weed mini`) as the default local object service, with pinned release/image and persistent data plus internal metadata. Use one service for separate private dataset, MLflow, Mimir, Loki and Tempo buckets. Keep ordinary S3 clients and deployment endpoint/region/credential/bucket/addressing configuration; do not couple application code to native SeaweedFS filesystem/filer interfaces.

Logical artifact IDs/digests and published manifest bytes remain independent of storage placement. Generate consumer-reachable signed URLs when needed; never persist them as canonical identity. Qualify required S3 operations and actual integration clients on every enabled backend. Cloud storage is not assumed fully S3-equivalent; pin/test capability differences and fail unsupported profiles explicitly.

For existing data, copy/verify referenced bytes and use a documented write-pause or another justified cutover protocol before changing placement/configuration. Preserve source data through validation/rollback and account for writes after resumption. Follow supported integration-native bucket/layout migration. No automatic dual-write framework or production SeaweedFS cluster is required.

## Consequences and verification

Single-node local persistence is not redundancy. Internal storage metadata and bounded WAL/queues require supported backups/recovery; S3 blocks alone do not prove recent-write durability. Pin/test the OSS image, CPU platform, configuration and actual client behavior before implementation readiness claims. See [Local S3 storage](../../infra/local-object-storage.md), [Ownership](../../infra/storage-ownership.md) and [Testing](../TESTING.md). This decision does not install software, deploy services or migrate data.
