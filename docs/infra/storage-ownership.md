# Storage and service ownership

## Target and implementation boundary

Share PostgreSQL and S3-compatible infrastructure while assigning one authority to each kind of state. This is the documented deployment target, not a provisioned configuration or measured capacity claim. Keep separate databases/users and private buckets/prefixes where ownership, credentials and retention differ; that is logical separation, not a reason to start another server for each integration.

| Owner | Durable state | Shared backing |
|---|---|---|
| Verdant domain services | Registry, revisions, operations, effect/usage receipts, retention references, audit and outbox/inbox | Platform database on PostgreSQL |
| Grafana OSS | Dashboard/user/data-source/alert configuration | Own database/user on PostgreSQL; no embedded SQLite |
| Keycloak | Identity/client/session state | Own database/user on PostgreSQL |
| Prefect | Orchestration metadata | Own database/user on PostgreSQL where supported; Operations remains Verdant's result authority |
| MLflow | Experiment/run/model tracking metadata | Its own supported metadata database on PostgreSQL; artifacts in its private S3 bucket |
| External Label Studio distribution | Native task/annotation/contributor state | Its own supported database/storage; canonical accepted revisions belong to Verdant |
| Mimir | Prometheus-compatible long-term metric blocks | Private metrics bucket in the existing S3-compatible service |
| Loki | Long-term log data | Private logs bucket in the same S3-compatible service |
| Tempo | Long-term trace data | Private traces bucket in the same S3-compatible service |
| Verdant artifact services | Sample bytes, manifests, report/derived artifacts | Private dataset/artifact buckets in the same S3-compatible service |

Use supported integration database engines/schema ownership; sharing infrastructure does not permit cross-service table mutation or skipping a service's migration/backup requirements. Budget combined database connections and object/compute capacity so telemetry cannot starve publication or interactive requests.

The default local object server is [SeaweedFS OSS](local-object-storage.md), shared across these buckets. Native internal metadata needed by that server stays owned by it; it is not a competing domain registry or an extra application database service. Cloud providers may replace this local endpoint after per-client compatibility and migration proof.

## One path and purpose per responsibility

- The OpenTelemetry Collector receives application OTLP and scrapes selected infrastructure metrics. It exports each signal once to Mimir/Loki/Tempo. Do not deploy Alloy, Prometheus scraping/storage or another Collector pipeline for the same source unless a concrete collection/isolation need exists; separate deployment instances may be needed across network/cluster boundaries without double-ingesting a source.
- Grafana queries these signal backends and owns initial alert evaluation/notification. Do not configure equivalent Prometheus/Mimir/Loki rules and duplicate notifications for the same alert. Backend components still needed by their supported deployment mode are not replaced with custom equivalents.
- Mimir replaces the standalone Prometheus metrics store; do not add Thanos or a second metrics database. Prometheus-format metrics and PromQL remain useful interfaces. A PostgreSQL data source does not turn PostgreSQL into the metrics TSDB.
- Verdant owns canonical datasets, version identities, quality-gate decisions, operations and durable audit. Prefect/MLflow/Label Studio retain the state needed to do their jobs, with explicit IDs/lineage linking back. Their UI projections/metadata are not competing canonical registries.
- Experiment quality/cost evidence and effect receipts are durable domain records, not reconstructed from best-effort traces. MLflow may present those results; name its authority/projection boundary and avoid billing a replayed/shared effect twice. Operational latency counters serve monitoring, not an independent experiment-accounting ledger.
- Keep model outputs, labels and raw sample payloads in artifacts/appropriate domain records, not duplicated wholesale into logs or metric labels. Detailed telemetry must not become a second private dataset store.
- Provision optional integrations only when their capabilities are used. Do not duplicate Keycloak identity, native annotation editors, orchestration, database servers or object stores just to complete a dashboard profile.

## Disk still has a bounded recovery role

Prometheus itself does not use S3 as its native TSDB. Mimir supplies the object-backed metrics layer. Its selected mode, Loki/Tempo and the Collector can still require local WAL/queues/cache/scratch; these are recovery/performance state, not separate long-term signal archives. Persist buffers where loss bounds require it, cap disk/memory use and test crash/restart and object-store outages. Object storage is the long-term backing, not a promise of zero local writes or zero recent-data loss after an unreplicated failure.

Do not mount S3/FUSE as a substitute for a database WAL or treat a container filesystem as persistent. Review supported deployment modes and recovery semantics before claiming HA. The small profile does not automatically introduce Kafka, Redis, distributed Mimir or a separate bundled MinIO service; any such dependency requires a real supported-mode/workload need and explicit ownership.

## Acceptance

Inspect rendered Compose/Kubernetes resources: one selected PostgreSQL service and object-store service/endpoint, Grafana's PostgreSQL backend, Mimir object storage and no standalone Prometheus/duplicate metrics archive. Verify each service's database/bucket ownership and actual mode requirements.

Emit a known metric/log/trace and verify one intended ingestion path and correlation; test an alert receiver for nonduplicate notifications. Restart components and interrupt the object store; measure bounded buffering, visible telemetry loss/recovery and unaffected domain commits. Restore configuration/databases plus retained objects coherently. Configuration review alone does not prove runtime behavior.

See [Grafana integration](grafana-keycloak.md) for protocol/authentication and official references, and [Architecture](../engineering/ARCHITECTURE.md) for domain ownership.
