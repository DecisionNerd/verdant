# Internal organizational Kubernetes deployment

Kubernetes is a deployment target for the self-hosted platform, alongside [local Compose](docker-compose.md). This is not a SaaS tenancy/identity design. Keycloak is the single organizational identity authority; [the service integration matrix](local-auth.md) defines native SSO, machine access, proxy boundaries and edition/plugin prerequisites.

**Status:** this guide defines target rollout and acceptance. No Kubernetes manifests, Helm release, Keycloak integration or cloud resources are created by the documentation change.

## Minimal topology

Use the existing cluster's Ingress/Gateway and TLS/DNS facilities. Deploy static frontend/API, bounded workers, Keycloak, persistent PostgreSQL and qualified S3-compatible storage. Use persistent volumes or operated databases/stores appropriate to recovery requirements; container filesystems are disposable. Keep separate database ownership for platform, identity, orchestration and enabled integrations. Managed cloud databases may support this self-hosted application; they do not imply a SaaS identity provider.

Self-host Prefect, MLflow, Label Studio and the Grafana OSS observability stack (Mimir, Loki, Tempo and the OpenTelemetry Collector) when enabled; pin compatible images/plugins/editions. Follow the [Grafana deployment contract](grafana-keycloak.md) for persistent state, private endpoints and supported topology; pod replicas alone do not establish high availability. The full dashboard profile requires the SSO capabilities documented in the matrix. The core profile does not pretend that an unavailable enterprise integration is already working. Configure optional integration ownership/state/backups without making core startup depend on every dashboard.

## Rollout

1. Choose namespace, stable application/auth/dashboard HTTPS names, TLS trust and database/object locations. Establish backup/restore and artifact retention before publication. Use one IaC/release owner per resource; see [Pulumi](pulumi.md).
2. Provision persistent PostgreSQL and object storage. Deploy Keycloak with the supported production configuration and its own persisted database, using one pinned Operator or existing deployment mechanism. Bootstrap realm/clients/groups and exact callback URLs through a repeatable, secret-safe process.
3. Run a single controlled Alembic migration Job. Deploy API/frontend and worker Deployments using pinned images, health probes, graceful shutdown and separate connection/concurrency budgets. Domain services remain shared; no mandatory service mesh or worker HTTP boundary is introduced.
4. Apply the service-by-service native SSO/plugin/proxy configuration. Enable only qualified dashboard profiles. Restrict proxied upstream entry, strip spoofable identity headers and verify noninteractive API and WebSocket paths. Configure Keycloak-backed backend sessions for the frontend; no frontend provider secret or second password database.
5. Register Prefect execution and service clients; configure integration machine credentials and OTLP ingestion separately from browser sessions. Secrets never appear in frontend assets or ordinary logs. Limit gateway/identity outages to work that actually needs new login/refresh.
6. Validate [cluster SSO](local-auth.md) and the [common dataset lifecycle/recovery acceptance](README.md) through the real Ingress hostnames, then record source/image/edition/plugin versions and observed budgets. Test rollout/restart and recover a database/artifact backup before claiming fault tolerance.

Roll API/worker/frontend versions back independently while preserving compatible schemas, stable principal mappings, receipts and immutable artifacts. Keycloak upgrades include database compatibility constraints: test backups/upgrade/recovery before changing the identity runtime. Deleting a Helm release/namespace is not application rollback or an identity migration.

## Acceptance boundary

One Keycloak login must enter every enabled dashboard as the correct individual, and service/API access must work without a browser. A proxy plus a second native password is a failure of the target experience. Readiness must identify missing license/plugin support rather than automatically purchasing it, sharing privileged accounts or weakening downstream authorization.

Provider-specific compute/storage steps remain in [GCP](gcp.md), [AWS](aws.md) and the other [infrastructure guides](README.md). Kubernetes support requires its own tested manifests; working cloud-container deployment alone is not proof of cluster integration.

## Sources

- [Keycloak basic Operator deployment](https://www.keycloak.org/operator/basic-deployment)
- [Keycloak Operator installation and upgrade considerations](https://www.keycloak.org/operator/installation)
- [Kubernetes Deployment rollout](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/)

For object storage, use one qualified existing S3 endpoint or an intentionally operated SeaweedFS deployment. Local `weed mini` is not a Kubernetes HA topology. Disable optional charts' bundled object servers when using the shared service; follow [backend qualification and cutover](local-object-storage.md) before moving retained data or switching clients.
