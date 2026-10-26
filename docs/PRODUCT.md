# Product

Verdant is an internal organizational platform, self-hosted with Docker Compose or Kubernetes and unified by Keycloak identity. It manages datasets as durable assets independent of a labeling project, model, or experiment. Teams ingest raw data, curate samples, review annotations, validate quality, publish reproducible snapshots, and connect evaluations and models back to exact dataset versions.

**Status:** accepted target design; implementation readiness is not asserted.

## Audience and jobs

| Audience | Job |
|---|---|
| Dataset owners | Discover, govern, version, publish, and deprecate datasets |
| Annotators and reviewers | Label exact sample revisions and record review provenance |
| ML practitioners | Consume reproducible splits, associate MLflow runs, compare evaluations |
| Agents and automation | Use typed SDK/REST/MCP operations within explicit permissions |
| Operators | Run workflows, inspect failures, recover state and artifacts, audit access |

## Product principles

- Dataset identity is stable; drafts are mutable; published versions are immutable.
- Humans and agents invoke the same authorized application services.
- PostgreSQL owns operational truth; S3-compatible storage owns large artifacts.
- Workflow engines and integrations do not become competing dataset registries.
- Users see real operation state, quality gates, lineage, and actionable failures.
- Favor direct, efficient execution and graceful degradation. Introduce transport isolation, projections and sharding when real workloads justify them, while retaining authorization and immutable-data integrity.

Document extraction may become one DataChain transformation. It does not define the platform's primary identity, storage model, or lifecycle.

## Scope

Dataset and schema registries, sample and annotation revisions, version-specific membership and splits, lineage, quality gates, publication, operations, identity/access, audit, and integrations with Prefect, DataChain, Label Studio, MLflow, and Evidently.

The [architecture](engineering/ARCHITECTURE.md) defines responsibilities. The [requirements](REQUIREMENTS.md) define acceptance.

## Non-goals for the initial platform

- Public SaaS tenancy, subscriptions or SaaS identity-provider integration.
- Premature microservices or a second business backend in the frontend.
- Distributed multi-region database writes; PostgreSQL is the initial database.
- A replacement for Label Studio's annotation editor or MLflow's model registry.
- A bespoke workflow engine, message broker, or vector-search platform.
- Promising that existing document-digestion commands implement dataset workflows.

## Success measures

Measure time to first validated published version, reproducible version reads, annotation review throughput, quality-gate outcomes, operation completion/recovery, and cross-interface authorization parity. Establish numeric SLOs and performance budgets from representative workloads before launch; no unsupported numeric target is claimed here.
