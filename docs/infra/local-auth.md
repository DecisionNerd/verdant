# Self-hosted organizational identity with Keycloak

## Scope and current status

Verdant is an internal organizational platform deployed with Docker Compose or Kubernetes. **Self-hosted Keycloak is the single human identity and first-party service-token authority in both targets**, not a local stand-in for Clerk, WorkOS or another SaaS identity product. One deployment uses one organizational realm; dataset/project namespaces remain authorization boundaries, not a public SaaS tenancy or subscription model. An organization may federate an existing directory through Keycloak without changing application login contracts.

The same person signs into the local frontend and enabled dashboards using the organizational Keycloak session. No duplicate passwords, shared dashboard admin accounts or second application login are acceptable as the completed cluster-SSO experience. Services retain their own authorization rules; one identity does not mean one unrestricted token or universal administrator role.

**Implementation status:** the Python OIDC backend, React/Vite login shell, stable principal mapping and strict REST/MCP service-token path are implemented; see the [qualification record](../engineering/IDENTITY-QUALIFICATION.md) and [operator runbook](identity-operations.md). Canonical host/container/pod issuer routing has passed an isolated fixture. Complete platform Compose and Kubernetes application deployment remain separate acceptance work. The root Compose still describes the earlier digestion runtime. Full-cluster SSO has edition/plugin prerequisites below. No license is purchased or assumed available.

## Frontend, API and internal identity

Use backend-managed authorization-code OIDC login with Keycloak, state/nonce and PKCE through the chosen supported library. FastAPI owns the browser session; React calls the session/API endpoints and holds no confidential client secret. Use secure HTTP-only cookies for shared deployments, deliberate CSRF/origin protection and exact callback URLs. The frontend's application permissions come from shared domain services, not from untrusted headers or browser state.

Map `(issuer, subject)` to a stable internal principal ID. Dataset ownership, memberships and audit reference that internal ID. Email/display name are editable attributes, not identity keys. Keycloak groups/client roles grant organizational entry and broad operator roles; domain services own dataset/project permissions. Seed owner and denied-user fixtures without creating a second password database.

Automation uses separate Keycloak confidential service clients and audience-scoped access tokens for the intended Verdant API/MCP resource. CLI/MCP user sessions use a supported non-browser or authorization-code client flow; never give an agent a human password or a browser session cookie. Validate issuer, signature, audience and expiry; ID tokens are not API access tokens. Cache discovery/JWKS and authorize bounded commands/batches locally. Refresh/reauthentication happens at normal boundaries, not through remote identity introspection per sample.

## Cluster integration matrix

| Component | Required integration | Completion evidence / prerequisite |
|---|---|---|
| React frontend + FastAPI | Keycloak OIDC through the backend-owned session | Real browser login, app session/logout and denied-user checks |
| FastMCP / SDK / first-party workers | Keycloak user/service access tokens plus domain authorization | Intended audience accepted; wrong audience/scope denied; jobs retain service/initiating-actor correlation |
| Keycloak admin console | Keycloak admin client/roles | Only designated identity operators can administer the realm |
| Label Studio Community | External OSS distribution with stock UI and Keycloak SSO | Consume a qualified pinned image; verify individual contributor identity, admission/removal and machine access. Community project visibility remains a deployment trust boundary |
| Grafana OSS | Built-in Generic OAuth with Keycloak | Group allowlist, strict role mapping, individual accounts and session/removal lifecycle verified; no fork or paid SSO dependency |
| MLflow | Pinned, qualified OIDC auth plugin using Keycloak | MLflow documents the community `mlflow-oidc-auth` plugin; test UI/API, groups/permissions and client authentication with the chosen versions |
| Self-hosted Prefect UI/API | Keycloak-authenticated reverse proxy such as OAuth2 Proxy | Browser SSO and noninteractive API/WebSocket paths tested; internal operations remain authoritative in Verdant |
| DataChain / Evidently processing libraries | Execute inside authenticated application/worker context | No separate user login when used as libraries; any separately hosted UI must be added to this matrix before exposure |
| PostgreSQL / object storage | Native database/storage credentials tied to service roles | Data-plane protocols keep their supported authentication; they do not accept a browser Keycloak cookie. Their admin consoles, if exposed, require a qualified SSO route or stay unexposed |
| OTLP collectors / integration APIs | Supported machine credentials or validated Keycloak tokens where supported | No interactive login on ingestion paths; tokens/keys stored server-side and scoped to the actual endpoint |

Keycloak authenticates every person and first-party service identity; it does not turn every database or third-party API into an OIDC resource server. Integration adapters may hold application-issued API credentials when that component requires them. Audit connects those service calls to the initiating actor; API keys are not a second human login system.

See the [Grafana OSS–Keycloak integration design](grafana-keycloak.md) for built-in OAuth configuration, account linking, role admission and session/removal qualification.

### Edition and proxy boundaries

Label Studio authentication design, code and release maintenance belong to its external OSS repository. Verdant consumes the tested image and integration contract. Community users share project visibility; do not export data into an instance whose admitted audience is broader than the dataset permits, or infer project ACLs/blind-review isolation from SSO. Grafana uses its OSS distribution and built-in Keycloak/Generic OAuth integration; no custom authentication provider, fork or paid SSO feature is required. Its private telemetry backends need separate machine authentication and network restrictions. Until the documented acceptance passes, report the optional dashboard as unqualified. Do not silently purchase software, share an admin login or describe a proxy gate plus a second password as SSO.

Use native SSO where it preserves per-person identity. For Prefect's proxy path, restrict dashboard entry to designated workflow operators because gateway group admission does not invent fine-grained native per-user authorization. Keep user-facing dataset operations in Verdant's authorized UI. MLflow requires plugin/version qualification, not a claim of native built-in OIDC. Avoid a new universal auth broker or wrapping every API in a browser redirect.

At a proxy, strip untrusted identity headers and configure the upstream to trust only that proxy where supported. Prevent direct host/Ingress bypass of proxied dashboards. API clients receive an explicit noninteractive authentication path and 401/403 on failure, not login HTML; never use an unauthenticated path exception for convenience. Validate issuer/audience before permitting a service-token route. A proxy's successful authentication alone does not establish the downstream user's identity or permissions.

## Bootstrap once, use the whole cluster

Bootstrap the organizational realm, separate clients/callbacks/audiences, organizational groups and service clients, then configure each enabled native integration/proxy. Generate secrets into ignored local files or Kubernetes Secrets/your existing secret manager. Keep only nonsecret templates tracked. Plugin/bridge images and component versions are explicit deployment inputs, never implicit downloads or trial activation.

Provision/map individual downstream identities using each supported SSO/JIT/admin API. Preserve the link to the immutable Keycloak subject where supported and record required provider-specific linking rules. Do not rely solely on matching email, especially for existing/admin accounts. Provisioning, deprovisioning and service-role mappings must be repeatable without deleting users or reassigning ownership. Bootstrap admin credentials are distinct recovery controls, not normal user access. Document audited operator recovery without exposing a public fallback login.

The delivered setup must create/update every enabled integration before announcing readiness. A visible service directory offers the actual local/cluster dashboard URLs and readiness state. Login to one service establishes the Keycloak session used by the next service; applications may establish their own cookies without asking for another password. Disabled/unqualified dashboards are reported explicitly rather than linked as working.

## Compose hostname and persistence

Use one canonical issuer URL reachable by browser and containers. For example, map `auth.verdant.test` to loopback on the host and use it as a Compose network alias for Keycloak, with matching served/published port and configured hostname. Document/bootstrap the host mapping or supply a tested equivalent proxy/DNS arrangement. Do not rewrite issuer values between `localhost` and service DNS or relax issuer validation to fix routing.

Pin Keycloak and use the supported realm import path for initial development setup. Persist its database; separate platform/identity/orchestration databases can share one local PostgreSQL instance. Realm import is not synchronization of an existing persisted realm. Repeated starts update supported configuration idempotently and preserve subjects, app accounts and data. Local loopback HTTP/start-dev are development settings; shared organizational use follows normal HTTPS/server settings.

## Kubernetes organizational deployment

Deploy the same realm/client/claim/session contracts with stable HTTPS names, persistent Keycloak PostgreSQL and the supported production server configuration. Use a pinned supported Keycloak Operator or one existing managed deployment path; the Operator does not provision its database. Do not install competing Helm/operator controllers for the same resources. Map Ingress/Gateway routes for frontend/API/auth and dashboards; internal DNS/routing must resolve the same public issuer and trust its certificate chain.

Browser SSO uses native callbacks or the qualified proxy route; machine integrations use service endpoints/credentials with no login redirects. Dashboard upstreams stay reachable only through their intended authenticated entry point. Standard Kubernetes Services, Ingress/Gateway and appropriate network policy are sufficient; do not introduce a service mesh or per-request remote identity dependency just to obtain SSO. Size Keycloak/database and gateway capacity for actual demand. Preserve already-valid sessions/token verification during transient outages within their normal expiry; new login/refresh fails clearly rather than bypassing authentication.

See [Kubernetes deployment](kubernetes.md) and [Compose deployment](docker-compose.md) for rollout and proof.

## End-to-end SSO acceptance

For every enabled profile, record image/plugin/edition versions and demonstrate:

- One organizational user signs in through the Verdant frontend, then opens each enabled dashboard without another credential prompt. The downstream account is that person with the intended role; Label Studio annotations retain the actual annotator identity.
- A denied or deprovisioned user cannot enter protected dashboards or access dataset resources. Verify session expiry/revocation behavior per integration; Keycloak logout alone is not proof all downstream sessions were cleared.
- Machine clients reach API/MCP and integration endpoints without browser redirects; wrong audience/scopes, expired tokens and spoofed proxy identity headers are rejected.
- Direct backend/dashboard bypass is unavailable, while bounded machine/OTLP/data-plane traffic continues through its intended authentication route.
- Restart/update Compose containers or roll Kubernetes pods: subjects, memberships, plugin mappings and published datasets persist. Test signing-key rotation, idle/SSE/WebSocket reconnect and identity/proxy outages with bounded recovery.
- Application and downstream logout/session lifetimes are documented and tested. Do not promise immediate global logout unless each application's supported logout/revocation integration actually proves it.

Full-cluster SSO is complete only when every enabled row passes. An auth container being healthy, a gateway login, or a realm export is not equivalent proof.

## Sources checked 2026-10-25

- [Keycloak container/realm bootstrap](https://www.keycloak.org/server/containers)
- [Keycloak canonical hostname](https://www.keycloak.org/server/hostname)
- [Keycloak Kubernetes Operator deployment and database ownership](https://www.keycloak.org/operator/basic-deployment)
- [Label Studio Community access model](https://labelstud.io/guide/signup)
- [Grafana Keycloak OAuth configuration](https://grafana.com/docs/grafana/latest/setup-grafana/configure-access/configure-authentication/keycloak/)
- [MLflow SSO plugin/proxy guidance](https://mlflow.org/docs/latest/self-hosting/security/sso/)
- [OAuth2 Proxy Keycloak OIDC provider](https://github.com/oauth2-proxy/oauth2-proxy/blob/master/docs/docs/configuration/providers/keycloak_oidc.md)
- [OAuth2 Proxy configuration and bearer-token validation](https://oauth2-proxy.github.io/oauth2-proxy/configuration/overview/)
