# Deploy on Google Cloud

**Target recipe, not a verified Verdant deployment.** Use [shared prerequisites and acceptance](README.md). Start with Cloud Run for the HTTP API/MCP and continuous worker compute selected independently; use Cloud SQL for PostgreSQL and qualified object storage.

## Placement and object compatibility

Cloud Run services handle HTTP requests, jobs run finite tasks, and worker pools handle continuous pull/event-driven work. A continuous dispatcher/Prefect worker belongs on a suitable worker pool or Compute Engine VM, not as an untracked background coroutine in a request-serving container. A Cloud Run job can run a migration or a finite processing task when the Prefect adapter explicitly invokes/reconciles it. Provider execution retries still reuse domain effect receipts.

The simplest storage path preserving the current contract is a qualified S3-compatible backend. Google Cloud Storage offers an XML interoperability API with HMAC credentials for supported compatible clients; it is not identical to S3. If choosing GCS, qualify the actual client operations, endpoint/signing, checksums, pagination, multipart behavior and signed URLs before treating it as supported. A native GCS adapter is a separate implementation choice if interoperability is insufficient. Do not promise drop-in equivalence or silently change manifest schemas.

## Procedure

1. Select project, region and billing owner; enable the chosen compute, Artifact Registry, Cloud SQL and secret/storage services. Create scoped service accounts for API, workers and deployment. Put compute/database near one another with reviewed network connectivity.
2. Provision Cloud SQL PostgreSQL, database/users, recovery settings and connection path. Provision the artifact store and verify application compatibility. Publish pinned API/worker images to Artifact Registry.
3. Deploy the API image as a Cloud Run service using its runtime contract: bind the configured port, configure readiness/startup checks, secrets and database connection method. Decide ingress and invocation policy deliberately: browser OIDC users do not automatically hold Google Cloud IAM invocation credentials. Retain application-level authorization.
4. Set maximum instances/concurrency and connection pools together so scaling cannot exhaust Cloud SQL. Run migrations once through a Cloud Run job or controlled release runner with the same schema/network access; wait for success before switching API traffic.
5. Deploy continuous workers on a worker pool where its current region/features fit, or a managed VM process otherwise. Register Prefect execution and configure service identities, secrets, pool budgets and graceful checkpoint recovery. Do not assume an HTTP service's request-driven scaling monitors the outbox queue.
6. Deploy the Vite build to static hosting with HTTPS and SPA navigation (for example a dedicated static-hosting service/CDN, or a static-server container). Configure public API origin, OIDC redirects and SSE behavior. Follow the documented Cloud Run request/streaming limits; durable status allows reconnect without rerunning work.
7. Run shared acceptance and record service revisions, worker deployment/image, database recovery settings, storage qualification and costs. Exercise worker termination and database connection saturation before increasing scale.

## Recovery and IaC

Shift traffic back to a compatible Cloud Run revision and roll worker images independently; preserve compatible schema and durable state. Test Cloud SQL plus artifact restore, not merely a service revision rollback. Use the Pulumi GCP provider for supported resources; qualify worker-pool resource coverage in its pinned version, or keep native deployment ownership explicit. Budget database, worker capacity, API compute, object requests/transfer, telemetry and model calls separately.

## Sources

- [Cloud Run service/job/worker-pool resource model](https://docs.cloud.google.com/run/docs/resource-model)
- [Cloud Run container contract](https://docs.cloud.google.com/run/docs/container-contract)
- [Connect Cloud Run to Cloud SQL PostgreSQL](https://docs.cloud.google.com/sql/docs/postgres/connect-run)
- [Cloud Storage interoperability](https://docs.cloud.google.com/storage/docs/interoperability)
- [Cloud Run worker-pool deployment](https://docs.cloud.google.com/run/docs/deploy-worker-pools)
