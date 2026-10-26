# ADR-0005: Practical identity, durable audit and asynchronous telemetry

- **Status:** Accepted (target architecture)
- **Date:** 2026-10-25
- **Decider:** Repository owner

## Context

Access control and operational visibility must preserve data integrity without making every sample or internal call depend on a remote identity/telemetry service.

## Decision

Use self-hosted Keycloak OIDC/OAuth 2.0 identity validation with standard libraries and shared namespace/resource authorization for human, service and agent commands. Validate callbacks and ingestion boundaries; keep integration secrets backend-only. Recheck applicable access at result acceptance/publication and credential renewal. Storage roles or temporary URLs fit deployment needs, with explicit revocation/expiry limits.

Keep authoritative audit in PostgreSQL. Propagate request, operation, event and external-run correlation through the OpenTelemetry Collector to Mimir (Prometheus-compatible metrics), Loki (logs) and Tempo (traces), queried through Grafana OSS. Export telemetry asynchronously using bounded buffers and visible loss counters; telemetry outages do not block domain commits. Never log credentials, signed URLs or raw private sample/annotation payloads.

Grafana uses its own database/user on the existing PostgreSQL service. Mimir stores long-term metric blocks in the existing S3-compatible service alongside separately owned Loki/Tempo/artifact buckets. Use one Collector path per signal and no standalone Prometheus server/second metrics archive. Retain bounded WAL/queues where the supported mode requires recovery state. Avoid duplicate alert evaluation and preserve domain/experiment accounting authority separately from best-effort telemetry. See [Storage ownership](../../infra/storage-ownership.md).

## Consequences and verification

Current authorization applies to historical versions. Standard token validation, namespace checks and callback validation remain required on every transport path. Telemetry retention cannot erase audit. Numeric alert/SLO targets and deployment trust boundaries require measured qualification.

See [Architecture](../ARCHITECTURE.md), [Implementation](../IMPLEMENTATION.md) and [Testing](../TESTING.md) for the complete contract and proof plan.

The frontend uses a backend-owned session, and enabled dashboards follow the native SSO/plugin/proxy integrations in [Organizational Keycloak](../../infra/local-auth.md). Organizational membership is distinct from dataset/project permissions; the deployment has no public SaaS identity/tenant-management path.
