# Local Docker Compose deployment

Docker Compose is a first-class internal organizational target alongside [Kubernetes](kubernetes.md), not just a convenient way to run dependencies. The local platform must exercise the same domain services, PostgreSQL schema, manifests, operation/effect receipts and OIDC authorization as cloud deployments. No special local registry or authentication bypass is required.

**Current status:** Docker is available on the checked development host, but the repository's root `docker-compose.yml` still describes the earlier Node/Next.js/Turso digestion runtime. It does not deploy the target dataset platform and contains no platform OIDC provider. This guide defines the required implementation and proof; it is not a claim that the new platform currently starts. See [organizational Keycloak and cluster SSO](local-auth.md).

## Target composition

| Service | Local role and persistence |
|---|---|
| `web` | React/Vite build or development server; only public configuration |
| `api` | FastAPI and FastMCP adapters over the shared Python application |
| `worker` | Continuous outbox/integration/Prefect worker entry point; resumable work, disposable scratch |
| `postgres` | Named volume for platform state; separate databases/users for identity and orchestration when sharing one local PostgreSQL instance |
| `objects` | SeaweedFS OSS single-node S3 service (`weed mini`), pinned image and persistent named data/metadata volume; same qualified S3 contract as cloud artifacts |
| `auth` | Keycloak with a bootstrapped organizational realm, persisted state and human/service identities |
| `prefect` | Local orchestration server with persisted metadata; worker connects using the supported pinned configuration |
| One-shot bootstrap/migrate | Idempotent database/bucket/realm preparation and a single Alembic migration execution |

Keep only the services necessary for the current lifecycle in the default composition. Registry-only milestones may omit execution services until that slice exists. Optional self-hosted profiles add Label Studio, MLflow and the Grafana OSS observability stack (Mimir, Loki, Tempo and the OpenTelemetry Collector) when testing those capabilities. Use the [Grafana deployment contract](grafana-keycloak.md) for small deployment modes, persistent state, private endpoints and built-in Keycloak OAuth. Each enabled dashboard must satisfy the [Keycloak integration matrix](local-auth.md), including required SSO editions/plugins; proxy login plus another native password is not completion. DataChain/Evidently execute with the application/processing image as appropriate. An offline fixture can prove the core lifecycle without paid model calls; real provider qualification and ADAM model comparisons are explicitly separate runs, never simulated benchmark claims.

Use `infrastructure/compose/` for the target files when implementing. The root Compose command should select or delegate to that target once ready; clearly retire the obsolete entry point at that point. Do not run two stacks against the same volumes or silently reuse legacy data as a migrated registry. Root Compose now delegates to the implemented registry/identity slice. The earlier composition is isolated in `docker-compose.legacy.yml`; existing data is not migrated or deleted. See the [local runbook](../../infrastructure/compose/README.md) for commands and bounded qualification.

The local object-store choice, bucket bootstrap, endpoint/signed-URL behavior and cloud migration proof are defined in [Local S3 storage](local-object-storage.md). Use one shared SeaweedFS service rather than deploying an integration-bundled object store.

## Implementation and operator sequence

1. Build API/worker images with actual Python entry points, OS dependencies and health contracts; produce the Vite frontend. Pin service/image versions and record the tested CPU architecture. Verify native dependencies on each claimed developer platform instead of forcing an untested architecture emulation setting.
2. Provide a documented local environment template and bootstrap that generates local secrets into ignored files, creates the required databases/bucket and bootstraps the Keycloak realm/clients/groups, backend frontend-session client and enabled dashboard SSO/plugin/proxy mappings. Repeated startup must preserve existing state and avoid duplicate data; never reset a volume as an implicit fix.
3. Give services stable Compose DNS names. Browser URLs use host-reachable addresses, while container-to-container connections use service DNS. For OIDC the issuer must be one canonical URL reachable consistently from both: follow [the hostname contract](local-auth.md). Do not use `localhost` to reach another container.
4. Use dependency health checks and a one-shot migration with successful completion before API/worker readiness. API readiness verifies required dependencies/schema; liveness does not restart the application merely because optional telemetry is unavailable. Workers also retry dependency startup with backoff; Compose start order alone is not a recovery mechanism.
5. Publish the implemented invocation in this guide and the root README. The operator path must be: copy/bootstrap local configuration → `docker compose config --quiet` → `docker compose up --build -d` → inspect readiness → run the lifecycle smoke command. These commands are the desired target interface; root `up` starts the registry/identity slice; execution/publication acceptance remains tied to later implemented workflows. Do not advertise a smoke script until it exists and passes.
6. Document public UI/API/MCP/auth locations, local user setup and service-client configuration without committing real credentials. Host port mappings may be overridden; no universal port cluster is required. Bind development endpoints to loopback unless intentionally sharing a test environment. Application services do not need access to the Docker socket to discover login links or manage other containers.
7. Stop with `docker compose down` while retaining named data volumes. A destructive reset is a separate, explicitly documented operation. Upgrade schemas through Alembic; image replacement and realm import do not perform a data migration automatically.

## Required local acceptance

Before describing local deployment as working, record the exact source/image versions and command outputs, then prove:

- A clean environment reaches readiness after the documented bootstrap/start sequence; a second start preserves registry/auth/artifact state.
- A real user signs in through Keycloak, enters the frontend and every enabled dashboard without a second credential prompt as the same individual, and creates a dataset/draft. Another user lacking permission cannot discover/read it. A separate service client exercises authenticated API/MCP access.
- A small fixture ingests/processes, validates, publishes an immutable manifest and resolves its exact bytes. Editing the draft during candidate preparation does not change the published candidate.
- Kill/restart a worker after an external effect succeeds; resume with receipt reuse rather than repeated effects. Restart the API and PostgreSQL and verify durable operation recovery and client status reconnect.
- Interrupt object storage, identity and optional telemetry in separate tests. Report required operations unavailable accurately; unaffected work continues where its dependencies permit. Cached valid identity material follows normal expiry; no outage enables an auth bypass.
- Saturate background concurrency within a representative local workload while interactive reads retain their measured budget. API/worker pools remain within local PostgreSQL capacity.
- Stop/recreate containers without deleting volumes; existing users, published manifests, effect receipts and artifacts still resolve. Exercise backup/restore separately from container restart.

This is an end-to-end acceptance gate, not satisfied by YAML validation, healthy containers or a successful login alone. Follow [shared deployment acceptance](README.md), [Testing](../engineering/TESTING.md) and [Publishing](../engineering/PUBLISHING.md).

## Sources

- [Docker Compose startup and dependency readiness](https://docs.docker.com/compose/how-tos/startup-order/)
- [Docker Compose networking](https://docs.docker.com/compose/how-tos/networking/)
- [Docker Compose profiles](https://docs.docker.com/compose/how-tos/profiles/)
- [Keycloak container and realm import](https://www.keycloak.org/server/containers)
