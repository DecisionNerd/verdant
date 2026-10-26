# Local S3 storage and cloud portability

## Decision and readiness

Use **SeaweedFS OSS** as the default local S3-compatible object service. Start with one pinned container/process using the upstream single-node `weed mini` mode and a persistent named volume holding all data/metadata required by that mode. Use the upstream OSS image/source, not an Enterprise distribution. Pin the tested image digest, license, CPU architecture, command/flags and persistence paths during implementation; do not use `latest` as a qualified release.

The project is Apache-2.0 licensed and offers a compact single-node S3 setup. Qualification for Verdant and its integration clients is still required. The target Compose/storage adapter is not implemented by this documentation change; no storage server is started, no bucket/data is migrated and no performance/availability claim is made. A single local disk/container is convenient persistent development storage, not redundant production storage.

## One object service, separate ownership

All local object clients use the shared `objects` service's S3 endpoint. Bootstrap private buckets for dataset/artifact data, MLflow artifacts, Mimir metrics, Loki logs and Tempo traces as their profiles are enabled. Use separate credentials/retention according to actual service ownership; do not create another object-store instance from every integration's sample Compose/Helm chart. The [ownership map](storage-ownership.md) defines which data each service controls.

SeaweedFS's native filesystem/filer/table interfaces are not application contracts. Clients use standard S3 SDKs/APIs. Keep its internal local metadata storage an implementation detail; sharing platform PostgreSQL does not require forcing the object server's internals into the platform schema. Store object bytes locally through the named volume, not container scratch. Use the ordinary S3 API for reads/writes, not S3/FUSE mounts for database files.

## Stable identity and endpoint configuration

Record stable artifact identity, byte length and SHA-256 separately from storage placement. A storage location resolves a configured store ID, bucket and object key; endpoints and credentials are deployment configuration. Published manifests pin logical artifact references/digests and schema version, not a particular local hostname or expiring signed URL. Cloud moves update authorized location mappings without changing historical sample/version IDs or published manifest bytes.

Configure endpoint, region, bucket, credentials and path-style/virtual-hosted addressing as the selected client/backend requires. Validate settings through the existing application configuration; no generic storage-plugin framework or per-cloud domain implementation is needed. Cloud workload identities may replace static keys through the SDK's supported credential chain.

Container clients and browsers may need different reachable addresses. Generate signed upload/download URLs for the actual client-facing hostname; signing an internal Compose address and then rewriting the URL breaks its signature. Test host/container DNS, CORS and signing at the boundary. Never persist signed URLs as canonical references or expose backend credentials to frontend assets.

## Required compatibility proof

Use one repeatable adapter contract suite against SeaweedFS and each enabled destination, plus service-specific tests with the actual pinned clients. Cover only the operations needed by the supported profile, but do not infer compatibility from a successful upload alone:

| Capability | Required proof |
|---|---|
| Basic objects | Create/use private bucket, Put/Get/Head, byte size, metadata and application SHA-256 verification |
| Large objects | Multipart create/upload/complete/abort, failed/abandoned upload cleanup, retries and bounded streaming memory |
| Listing | Pagination, continuation, prefixes and empty results without skipped/duplicate objects |
| Read behavior | Range reads and missing/corrupt content behavior where clients require them |
| Concurrency | Conditional writes/create-if-absent or a proven application-level alternative preserving immutable keys and idempotency |
| Deletion | Single/bulk delete where used, missing-object behavior, retained-reference protection and collector/finalizer races |
| Signed access | Host-reachable presigned upload/download with expiry/CORS; clients cannot silently substitute an endpoint |
| Failure/recovery | Restart with volume persistence, unavailable object service, retry reconciliation and complete backup/restore |
| Integrated clients | Verdant publish/consume, MLflow artifact round-trip, Mimir metric-block write/query, Loki log write/query and Tempo trace write/query after flush/restart |

Do not treat ETag as universally equivalent to SHA-256 or multipart content identity. Check checksum headers and signing behavior against the actual SDK/backend versions. Record a backend capability matrix; fail or mark the unsupported profile unavailable rather than disabling integrity checks or silently using another untracked store. Temporary WAL/queues/cache remain bounded and mode-specific; object storage does not eliminate required recent-write recovery state.

## Cloud change and existing data migration

A new empty environment changes deployment configuration and qualifies its destination; it need not run SeaweedFS when a cloud object service fits. AWS S3 is the direct API target; R2, Tigris and other S3-compatible services require the same contract proof. Google Cloud Storage interoperability is explicitly qualified, or a separately implemented native adapter is used; GCS is not assumed identical to S3. Self-hosting SeaweedFS on Kubernetes is an option, not a requirement or an HA claim.

Moving existing objects is more than swapping an endpoint. Inventory referenced artifacts/manifests and each integration's native object layout. Copy to the destination, verify byte counts/digests, then switch placement/configuration under a documented cutover protocol. Preserve immutable identifiers and metadata; do not republish or rewrite old manifests merely to change storage providers. Keep the source intact until validation and the rollback window pass.

For an initial migration, a deliberate bounded write pause is simpler than building mandatory dual-write infrastructure. Drain/checkpoint writers, migrate/verify the stable object set, update location mappings and client configuration, validate dataset plus integration reads, then resume. If writes resume, a later rollback must account for new destination writes; changing the endpoint back blindly is not a safe rollback. Use each Mimir/Loki/Tempo/MLflow version's supported migration/layout procedure rather than assuming arbitrary bucket renaming is transparent.

Back up both SeaweedFS object data and required internal metadata using its supported consistent method alongside platform PostgreSQL/reference state. Recreating a container is not a backup, and copying only payload files may not restore the store. Dataset and observability buckets have independent retention/restore needs, but share capacity budgets and actual storage infrastructure.

## Sources

- [SeaweedFS OSS project and single-node mode](https://github.com/seaweedfs/seaweedfs)
- [SeaweedFS S3 API](https://github.com/seaweedfs/seaweedfs/wiki/Amazon-S3-API)
- [SeaweedFS Docker Compose examples](https://github.com/seaweedfs/seaweedfs/wiki/Docker-Compose-for-S3)

Source review date: 2026-10-26. Feature claims remain subject to the selected release's contract tests; upstream examples are not Verdant deployment manifests.
