# Verdant

Verdant is being redefined as a **dataset-first production ML data platform**. Datasets are the primary managed assets; annotations, transformations, quality reports, experiments, and models attach to them.

**Documentation status:** these docs define the accepted target architecture. The Python registry foundation is available; the complete platform remains under implementation. Existing source, containers, commands, and deployments may still implement the earlier document-digestion tool. Do not treat them as a working platform installation.

## Target stack

| Concern | Decision |
|---|---|
| Human interface | React + TypeScript + Vite; TanStack Query/Table; React Flow |
| Human and system API | FastAPI: REST, OpenAPI, SSE, webhooks |
| Agent interface | FastMCP over the same authorized application services |
| Contracts and state | Pydantic v2; PostgreSQL; SQLAlchemy 2; Alembic |
| Execution | Prefect orchestration; DataChain processing; scalable workers |
| Annotation | Label Studio |
| Experiments and quality | MLflow; Evidently |
| Artifacts | S3-compatible storage |
| Identity | Self-hosted Keycloak for organizational Compose/Kubernetes deployments |
| Observability | OpenTelemetry Collector + Grafana OSS, Mimir, Loki and Tempo |

Start with a modular Python monolith and separately scalable workers. Keep bulk data out of API request bodies and database rows. Published versions pin exact revisions and immutable manifests.

## Read the design

- [Documentation map](docs/README.md)
- [Product](docs/PRODUCT.md) and [requirements](docs/REQUIREMENTS.md)
- [Architecture and domain contracts](docs/engineering/ARCHITECTURE.md)
- [Implementation slices and unresolved decisions](docs/engineering/IMPLEMENTATION.md)
- [Infrastructure and deployment](docs/infra/README.md)
- [Architecture decisions](docs/engineering/adrs/README.md)
- [Operator and agent documentation source](apps/docs/src/content/docs/)

## Documentation development

With the repository's pnpm toolchain installed:

```sh
pnpm install --frozen-lockfile
pnpm --filter @verdant/docs dev
pnpm --filter @verdant/docs build
```

These commands serve or build documentation only. See [Python foundation setup and proof](docs/engineering/PLATFORM-FOUNDATION.md) for the bounded registry backend. Root Compose starts the registry/identity slice described below; protected publication is implemented on the development dependency stack. The complete execution platform remains under implementation.


## Local registry and identity

Requirements: Docker Compose v2 with `include` support, Python 3, and uv for smoke checks.

```sh
python3 infrastructure/compose/bootstrap.py
docker compose config --quiet
docker compose up -d --build
uv run --locked python scripts/qualify_compose.py
```

Open http://127.0.0.1:18800. The realm starts without human accounts; follow the [local runbook](infrastructure/compose/README.md) for user provisioning, canonical issuer, credentials, endpoints, and qualification limits. The smoke creates only synthetic local registry/storage fixtures and a scoped service grant.

Stop with `docker compose down`; named volumes persist. Do not delete `.local/` or volumes during routine recovery. [Qualification evidence](docs/proofs/issue-12/compose-preflight.md) records the tested scope.

The old digestion composition remains in `docker-compose.legacy.yml`, with its original relative paths. It is not started by the root command. Existing legacy volumes and bind-mounted data are untouched. If operating a prior installation, use its original explicit project name with `docker compose -p ORIGINAL_PROJECT -f docker-compose.legacy.yml ...`; do not point the new stack at legacy data or run a reset as a migration.

The [registry workspace guide](docs/engineering/REGISTRY-WORKSPACE.md) covers catalog filters, metadata conflicts, exact sample/draft inspection and session behavior.

The [quality guide](docs/engineering/QUALITY-RULES.md) covers versioned policies,
sealed-candidate validation, resumable reports and exact offending-revision evidence.
The [publication guide](docs/engineering/IMMUTABLE-PUBLICATION.md) covers protected
artifact preparation and atomic immutable publication on the development stack.
Version consumption and archive controls remain under implementation.
