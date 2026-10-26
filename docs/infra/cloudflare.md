# Deploy with Cloudflare

**Recommended starting topology:** Cloudflare hosts the static React/Vite frontend and R2 artifacts; the Python API/workers and PostgreSQL run on one of the container/database targets in this directory. This preserves native dependencies and long-running worker behavior without rewriting the platform for the edge.

This is a target recipe. Follow [shared prerequisites and acceptance](README.md); no Worker, R2 bucket, Container or database is provisioned here. Cloudflare Containers are an alternative to qualify, not a claim that the entire platform runs unmodified in Workers.

## Procedure: static frontend and R2

1. Deploy the Python API/MCP and continuous worker compute using [Fly.io](fly-io.md), [Railway](railway.md), [GCP](gcp.md) or [AWS](aws.md). Keep PostgreSQL near that compute. Configure OIDC and the public API hostname there first.
2. Create a private R2 bucket and backend credentials with only necessary bucket access. Configure the application's S3 adapter with the R2 endpoint, region and credentials. Check the current R2 compatibility matrix for required operations/checksum behavior; verify multipart upload/download, digest handling, retries and object retention. Do not substitute D1 for authoritative PostgreSQL.
3. Build the target Vite frontend with only public API/origin values. Deploy its static output through Workers Static Assets, setting `assets.directory` to the actual build directory and `assets.not_found_handling` to `single-page-application`. A purely static frontend does not need to host the Python backend in a Worker.
4. Use the reviewed Wrangler configuration and pinned CLI from the deployment project; `wrangler deploy` publishes it. If a Cloudflare Vite plugin generates deploy configuration, use that workflow consistently rather than configuring two separate asset directories.
5. Configure custom domains, OIDC redirects, browser origin policy and upload/download CORS as required. With a same-origin API proxy, route API/MCP/SSE before SPA fallback; test streaming/timeouts and do not cache authenticated responses. Direct API origins are also valid and need no proxy service.
6. Run the shared lifecycle/restart acceptance and inspect cross-provider latency and transfer costs. Optional Cloudflare/OTLP monitoring loss cannot become a domain commit dependency.

## Containers alternative

Cloudflare Containers can host Linux container workloads, but must be qualified for the selected Python image, PostgreSQL connectivity, native dependencies, lifecycle, outbound calls, continuous polling and Prefect execution. Local disk is temporary; keep checkpoints in PostgreSQL and artifacts in R2. Container snapshots/FUSE do not automatically establish database durability or required throughput. Test sleep/restart/rescheduling and worker capacity against real workloads before replacing established worker compute. Cloudflare Workflows/Queues are not automatic substitutes for the selected Prefect/outbox contracts.

## Recovery and infrastructure ownership

Deploy previous compatible frontend/backend revisions independently; preserve database and artifact references. Verify bucket retention before enabling lifecycle deletion. Pulumi may own DNS/R2/Workers resources where its pinned provider supports them; if Wrangler owns Worker deployments, keep that boundary explicit and avoid competing ownership. Review current account limits and pricing; R2 S3 compatibility is not identical to all AWS S3 features.

## Sources

- [Workers Static Assets](https://developers.cloudflare.com/workers/static-assets/)
- [SPA navigation handling](https://developers.cloudflare.com/workers/static-assets/routing/single-page-application/)
- [R2 S3 API compatibility](https://developers.cloudflare.com/r2/api/s3/api/)
- [Containers concepts](https://developers.cloudflare.com/containers/concepts/)
- [Containers lifecycle/storage FAQ](https://developers.cloudflare.com/containers/faq/)
