# Infrastructure and deployment

Deploy the [dataset platform architecture](../engineering/ARCHITECTURE.md) using the smallest topology that fits the workload. These guides describe provider mappings and ordered deployment procedures. **They are not verified production deployments:** the target Python applications, images, startup/health contracts and Pulumi programs are not supplied by this documentation change. Existing digestion Docker/Compose files do not implement this target.

Provider capabilities were checked against official documentation on 2026-10-25. Confirm current availability, region support, quotas and prices before provisioning. No resources are created by these docs.

| Guide | Recommended starting placement |
|---|---|
| [Docker Compose](docker-compose.md) | First-class local lifecycle, persistent dependencies and restart/recovery proof |
| [Organizational Keycloak](local-auth.md) | One self-hosted identity authority and service-by-service SSO for Compose/Kubernetes |
| [Grafana OSS + Keycloak](grafana-keycloak.md) | Built-in OSS Keycloak OAuth, individual sessions, strict role admission and private telemetry backends |
| [Kubernetes](kubernetes.md) | Internal organizational deployment with the same Keycloak and domain contracts |
| [Local S3 storage](local-object-storage.md) | SeaweedFS local default, S3 compatibility suite and verified cloud cutover |
| [Storage ownership](storage-ownership.md) | Shared PostgreSQL/S3, one signal backend per purpose, no duplicate collection/storage |
| [Pulumi](pulumi.md) | Infrastructure state, provisioning ownership, secrets and repeatable promotion |
| [Fly.io](fly-io.md) | API/worker Machines, managed PostgreSQL, S3-compatible artifacts |
| [Railway](railway.md) | Separate API/worker services, PostgreSQL and external durable artifacts |
| [Cloudflare](cloudflare.md) | Static frontend/R2 with a container backend elsewhere; Containers as a separately qualified option |
| [Google Cloud](gcp.md) | Cloud Run API, continuous worker compute, Cloud SQL and qualified object storage |
| [AWS](aws.md) | ECS/Fargate API/workers, RDS PostgreSQL, S3 and static CDN frontend |

## Shared prerequisites

Before following a provider guide, produce immutable API/worker image digests, a React/Vite static build and supported database/event/manifest versions. Publish actual start commands and ports, liveness/readiness behavior, graceful shutdown and a single-run Alembic migration command. No speculative module names, image URLs or environment-variable names in these guides are installed application interfaces.

Use the self-hosted [Keycloak contract](local-auth.md) for organizational login on each compute target, including the enabled-dashboard edition/plugin prerequisites. Choose a region near PostgreSQL and artifacts; measure model-provider latency and cross-cloud transfer costs. Supply OIDC identity configuration, database connection settings, object endpoint/bucket/credentials, Prefect connection/work-pool configuration and OTLP destination through the application's implemented configuration contract. Only public API/origin settings belong in frontend builds. Keep secrets in the platform secret store or encrypted IaC configuration.

FastAPI/FastMCP may share a process. Keep long-running pollers and processing out of request-scoped background tasks: use persistent worker compute and Prefect execution with durable operations/checkpoints. Managed Python workers can invoke shared services directly. Set separate API/worker pool budgets and total database headroom; bound retries, batches and image-specific memory/concurrency.

Self-host Prefect/MLflow/Label Studio/Grafana OSS as needed by the slice. Use Keycloak across the frontend and enabled dashboards according to the [cluster SSO matrix](local-auth.md); SaaS identity/tenant management is outside this organizational deployment scope. Self-hosted integration metadata/databases need their own persistence and backups; they do not replace platform PostgreSQL. Do not install every integration just to launch the registry. DataChain/Evidently libraries run with processing/evaluation code as appropriate.

## Common rollout sequence

1. Establish account/project, region, billing ownership and the capacity/availability target. Select one infrastructure owner per resource; [Pulumi](pulumi.md) documents that path.
2. Provision PostgreSQL and private artifact storage first. Enable appropriate backup/restore and retention controls; leave automatic object expiry off for retained dataset artifacts. Verify required object operations, digests, uploads and reads with the chosen backend.
3. Publish pinned images. Configure API and workers with consistent schema/contracts, identities and connection budgets. Keep worker scratch storage disposable; only committed PostgreSQL/object state is authoritative.
4. Run Alembic once as a release job before compatible application rollout. Do not run competing migrations on each replica start. Use expand/contract migrations; do not attach schema migration to a health probe.
5. Deploy API/MCP, check readiness and OIDC callbacks, then start workers and register the Prefect execution deployment. Keep worker shutdown/restarts checkpointed; rolling restarts must not duplicate logical effects.
6. Deploy static frontend with the correct public API URL. Configure SPA navigation fallback, HTTPS, browser origin/cookie rules, provider redirect URLs and webhook URLs. If a proxy handles API/SSE, test its streaming and timeout behavior; never cache private API responses as static assets.
7. Run the acceptance below before promotion. Roll application image/traffic back if needed while retaining compatible schema, immutable data, effect receipts and outbox state. Infrastructure rollback is a separate reviewed change, not a reason to destroy data.

## Deployment acceptance and operations

Create → ingest → validate → publish → consume an authorized dataset version; deny an unauthorized principal. Publish a frozen candidate while editing continues. Kill/restart a worker after an external effect succeeds and verify receipt reuse, one logical outcome and actual incurred cost. Exercise SSE reconnect, optional telemetry failure, archived dataset behavior and API responsiveness under worker saturation.

Resolve manifests/digests after a coordinated restore. Check database pool saturation, worker memory, backlog/age, external dispatch uncertainty and artifact integrity. Record source/image versions, region, workload, costs, limits and unresolved provider gaps. Use [Testing](../engineering/TESTING.md) and [Publishing](../engineering/PUBLISHING.md) as release gates; this checklist does not establish current runtime readiness.
