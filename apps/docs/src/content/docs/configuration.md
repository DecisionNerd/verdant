---
title: Configuration
description: Target configuration ownership and security.
---

The platform's configuration contract is a design target. No new environment variables or production defaults are installed by this docs change.

| Configuration area | Owner / requirement |
|---|---|
| Identity | Self-hosted Keycloak issuer/client/audience configuration, backend browser session and human/service/agent scopes; shared resource authorization |
| PostgreSQL | Control-plane connection, scoped service roles, Alembic migrations and backups |
| Object storage | S3-compatible endpoint/bucket/region, scoped credentials, integrity and retention |
| Prefect / DataChain | Deployment identity, bounded concurrency, retry/reconciliation and environment pinning |
| Label Studio | Backend integration credential, callback verification, project/schema mappings |
| MLflow | Tracking/model-registry access and exact dataset-version association |
| Evidently | Versioned policy/reference inputs and report artifact location |
| OpenTelemetry Collector / Grafana OSS / Mimir / Loki / Tempo | OTLP collection, signal-specific private endpoints and machine credentials, Grafana Keycloak OAuth, sampling/redaction, persistence and retention |
| Frontend | Public API location and approved browser authentication configuration |

Keep secrets out of browser bundles, Git, telemetry and ordinary metadata. Validate callback endpoints and ingestion locations; artifact references do not grant access by themselves. Provision only the services needed by the accepted implementation slice.

Hosting, supported integration versions, deployment ports, resource budgets, retention and RPO/RTO remain explicit deployment decisions. Legacy `.env` values, local login defaults and 187xx ports do not define the new platform contract.

Use direct shared-service calls for managed Python workers where practical; API calls serve remote boundaries. Configure ordinary service/storage roles or temporary URLs to fit the deployment rather than imposing a custom capability system. Optional telemetry/search/integration failures degrade their own features, while authoritative operations remain available. Validate tokens with the standard provider library and authorize commands/batches without remote identity checks per sample.

Local Compose and organizational Kubernetes use the same Keycloak realm/client contract and per-service dashboard SSO matrix in repository `docs/infra/local-auth.md`. Provision native SSO/plugin/proxy paths for each enabled service; no second dashboard password or shared admin account is a completed integration.

The local object-store target is SeaweedFS OSS in one persistent service, shared by separate private dataset/MLflow/Mimir/Loki/Tempo buckets. Cloud backends change endpoint/region/bucket/credential/addressing configuration after compatibility checks; signed URLs use the consumer-reachable endpoint and are never stored as canonical references. Existing objects require verified migration, not just an endpoint change. Repository `docs/infra/local-object-storage.md` specifies the proof and cutover.
