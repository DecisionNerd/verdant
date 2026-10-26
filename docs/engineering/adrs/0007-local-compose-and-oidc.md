# ADR-0007: Organizational Keycloak SSO on Compose and Kubernetes

- **Status:** Accepted (target architecture)
- **Date:** 2026-10-25
- **Decider:** Repository owner

## Context

The platform serves an internal organization, with local Compose and Kubernetes deployment. Users need one identity across the frontend and enabled service dashboards; automation needs noninteractive access without public SaaS identity/tenant management.

## Decision

Use self-hosted Keycloak as the human and first-party service-token authority in both targets. The React frontend uses backend-managed OIDC login/session, while FastAPI/FastMCP enforce verified identity and shared domain authorization. Map issuer/subject to stable internal principals; dataset/project permissions remain platform state. Separate clients/audiences and organizational roles prevent a shared unrestricted credential.

Consume an externally maintained OSS Label Studio distribution with its stock UI and qualified per-person Keycloak SSO. Authentication implementation and release maintenance belong to that external repository. Use Grafana OSS built-in Keycloak/Generic OAuth with explicit group admission and strict role mapping, plus private Mimir/Loki/Tempo backends and the OpenTelemetry Collector. No custom authentication fork or paid SSO feature is needed. See the [Grafana integration design](../../infra/grafana-keycloak.md). Use a pinned qualified MLflow OIDC plugin and a Keycloak proxy path for self-hosted Prefect, with an explicit noninteractive client path. Preserve actual annotator/operator identity. Native database/storage and third-party API credentials remain protocol-specific service access, not additional human passwords. The service integration matrix defines acceptance and edition/plugin prerequisites.

Bootstrap supported realm/client/group and downstream mappings repeatably, with persisted identities and canonical issuer routing. Keep disabled/unqualified optional profiles explicit. Full SSO requires each documented edition/plugin or qualified OSS extension; no automatic purchase, shared admin account or proxy-plus-second-password workaround is authorized. One application-owned session and existing provider interfaces are preferred over separate custom auth services.

Compose validates the real local lifecycle and restart recovery. Kubernetes uses supported production Keycloak configuration, persistent PostgreSQL and stable HTTPS ingress. Both require correct individual dashboard SSO, denied-user checks, machine access, issuer/audience checks, logout/revocation qualification and dataset recovery. This records the target, not implemented manifests or a working integration.

## Consequences and verification

Keycloak is the chosen organizational deployment contract, not an interchangeable SaaS login placeholder. The full dashboard profile has self-hosted edition/plugin qualification prerequisites; a community-only install cannot be advertised as full native SSO without supported integration proof. See [Keycloak integration matrix](../../infra/local-auth.md), [Compose](../../infra/docker-compose.md) and [Kubernetes](../../infra/kubernetes.md).
