# Deploy on Railway

**Target recipe, not a verified Verdant deployment.** Use [shared prerequisites and acceptance](README.md). Deploy API and persistent workers as separate Railway services, optionally from the same pinned image with different start commands.

## Placement

Use PostgreSQL through a Railway database service or an external operated database; evaluate backups, restore and availability explicitly rather than assuming a database template provides managed HA. Use a qualified external S3-compatible bucket (or Railway object storage after checking its current capabilities). Keep dataset artifacts off container filesystems. A Railway Volume is persistent service-local storage with scaling constraints, not the artifact registry.

## Procedure

1. Create a Railway project/environment. Add API and worker services from the intended repository Dockerfile or immutable registry image. For a monorepo, set root/build context and Dockerfile path explicitly; Railway's `RAILWAY_DOCKERFILE_PATH` selects a nondefault Dockerfile. Do not import the digestion Compose file as the new platform.
2. Add PostgreSQL and configure data persistence/backups if Railway-hosted. Provision the artifact bucket separately. Reference connection/secret values using Railway service variables and its private networking where applicable. Keep public frontend config separate from backend secrets.
3. Set API and worker start commands to the implemented entries. The API binds `0.0.0.0` at the configured service port (`PORT` when using Railway's assigned port). Configure a real readiness path and public/custom domain. The worker requires no public HTTP domain; keep it continuously running rather than a scheduled cron task.
4. Use one controlled pre-deploy migration step or dedicated migration service/run before API rollout. Railway volumes are not mounted during pre-deploy; migrations here operate against network PostgreSQL, not local volume files. Do not run migrations independently in every replica.
5. Deploy API/MCP, then workers/Prefect execution. Set resource limits and connection budgets before increasing replicas. Keep API/worker services stateless so replicas do not contend over local state; verify current volume/replica restrictions before scaling stateful integrations.
6. Deploy a static-server service for the Vite output or use a CDN. Configure SPA fallback, public API origin, HTTPS/OIDC callback and webhook URLs. Run the shared acceptance against public routes, then restart/redeploy a worker to prove recovery.

## Recovery and cost

Railway's ephemeral filesystem is disposable. A database volume alone is not an off-service backup; document and test the chosen database recovery path and retained artifact integrity. Roll back compatible images while preserving database/schema and effect receipts. Account for allocated compute, persistent services/volumes, artifact storage/transfer and model-provider costs; account/plan limits are a deployment input.

Use Pulumi only where the selected provider covers the resource adequately; otherwise document Railway's native ownership and avoid two controllers changing the same service.

## Sources

- [Dockerfile deployment](https://docs.railway.com/builds/dockerfiles)
- [Services and images](https://docs.railway.com/services)
- [Volumes and limitations](https://docs.railway.com/volumes)
