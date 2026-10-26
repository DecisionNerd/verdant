# Deploy on Fly.io

**Target recipe, not a verified Verdant deployment.** First satisfy the [shared prerequisites and acceptance](README.md). Fly Machines suit containerized API and persistent workers; process groups let one image run different commands on separate Machines. This is a deployment choice, not a requirement to split domain services.

## Placement

Use Fly Managed Postgres or another operated PostgreSQL service near compute. Use Tigris or another qualified S3-compatible object store for dataset artifacts. A Fly Volume can support a stateful integration's own data when deliberately operated/backed up, but is not shared dataset object storage or automatic cross-machine replication. Serve the Vite build from a small static-server image or an external CDN.

## Procedure

1. Build the target API/worker image and publish its digest. Install/authenticate `flyctl`, select the organization/region and create app configuration using `fly launch --no-deploy` in the intended deploy directory. Review generated settings; do not auto-detect/deploy the legacy app by mistake.
2. Provision/attach Managed Postgres through the Fly dashboard/CLI, or configure the chosen external database. Provision artifact storage and verify S3 behavior. Store application secrets with Fly secrets; inspect the implementation's actual database/config keys rather than assuming generated values match them.
3. In `fly.toml`, define `api` and `worker` process groups with the implemented startup commands. Route HTTP services/health checks only to the API group on its actual bound port; keep the worker as a non-HTTP consumer. Separate apps are acceptable when images, credentials or release lifecycles differ.
4. Configure a single release/migration command using the implemented Alembic entry point, with the intended database access. Keep it compatible with old/new API instances. Provision dependencies before attempting the release.
5. Deploy the reviewed image/config with `fly deploy`. Scale API and worker groups independently (for example, `fly scale count api=2 worker=1` after sizing database pools). This is illustrative capacity, not a production availability guarantee.
6. Keep durable pollers running: HTTP autostop/autostart is not a queue-based worker autoscaler. Configure Machine restart/shutdown behavior and graceful checkpoint recovery explicitly. Test API/SSE behavior and OIDC redirects on the assigned hostname before adding the custom domain.
7. Deploy the static frontend with SPA fallback and the actual public API URL. Run shared acceptance, then record Machine/image IDs, process counts, region and recovery observations.

## Recovery and cost

Use provider-managed database recovery where available and prove restore with artifacts. Re-deploy a prior compatible image/config for application rollback; preserve workers' durable operations/receipts. Process group deletion can remove its Machines on deploy, so review the complete process list. Budget Machines, PostgreSQL, artifacts, transfer and external model charges separately; no free-tier or cross-region availability assumption is made.

If Pulumi owns these resources, use its reviewed updates; qualify the selected Fly provider's coverage or delegate specific CLI steps explicitly.

## Sources

- [Process groups and scaling](https://fly.io/docs/launch/processes/)
- [Fly Managed Postgres](https://fly.io/docs/mpg/)
- [Tigris object storage](https://fly.io/docs/tigris/)
- [Fly app configuration](https://fly.io/docs/reference/configuration/)
