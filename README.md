# Verdant

Verdant is being redefined as a **dataset-first production ML data platform**. Datasets are the primary managed assets; annotations, transformations, quality reports, experiments, and models attach to them.

**Documentation status:** these docs define the accepted target architecture. This documentation refactor does not implement the platform. Existing source, containers, commands, and deployments may still implement the earlier document-digestion tool. Do not treat them as a working platform installation.

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

These commands serve or build documentation only. Platform setup commands will be published when implementation acceptance passes. Existing Compose definitions are not the target deployment contract.
