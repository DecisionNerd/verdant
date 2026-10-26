> Supplied reference, architecture baseline v1.0. Preserved verbatim below. The supplied text ends mid-table in section 7 at “Concurrency | Optim”; it contains no further sections. Formatting defects, including the compact Python example, are source artifacts. Normative platform decisions and derived operational guidance live in [Architecture](ARCHITECTURE.md). This reference describes a target, not verified implementation.

# Production ML Dataset Platform — Reference Architecture

Architecture baseline v1.0

Observability amendment (2026-10-25): the owner-selected OSS stack is Grafana OSS, Prometheus, Loki, Tempo and the OpenTelemetry Collector. This baseline incorporates that replacement; the remaining supplied architecture and its original truncation are preserved.

This architecture defines a dataset-centric, production-grade ML data platform built around the technology decisions we've made: React/Vite, FastAPI, FastMCP, Pydantic, PostgreSQL, Prefect, DataChain, Label Studio, MLflow, Evidently, and Grafana OSS.

The fundamental design principle is:

Datasets are the primary managed assets. Experiments, annotations, quality reports, workflows, and models are connected to datasets—not the other way around.

The platform should support both human and agent-driven workflows, from raw data ingestion through annotation, validation, publication, evaluation, and continuous improvement.

## 1. Architecture at a glance

### Unified Experience Layer

React + TypeScript + Vite · Python SDK · External Clients · AI Agents

FastAPI

REST · OpenAPI · SSE · Webhooks

FastMCP

Agent tools · Resources · Auth

### Shared Domain Services

Dataset Registry · Versioning · Lineage · Quality · Authorization · Publication

Pydantic v2 contracts · SQLAlchemy · Transactional Outbox

Execution & Integration Layer

Prefect

Workflow orchestration

DataChain

Dataset processing

Label Studio

Human annotation

MLflow

Experiment tracking

Evidently

Quality and drift

PostgreSQL

State · Registry · Events · Projections

S3-Compatible Storage

Data · Manifests · Artifacts

Cross-Cutting: OpenTelemetry Collector + Grafana OSS, Prometheus, Loki and Tempo

Distributed traces · Metrics · Logs · Alerting

## 2. Architectural principles

1. Dataset-first: A dataset exists independently of a model, training run, or labeling project.
2. Immutable publication: Published dataset versions never change in place.
3. Schema-first: Pydantic contracts govern all service boundaries and canonical metadata.
4. API-first: Humans, agents, and systems operate through shared domain services.
5. Event-driven state: PostgreSQL holds authoritative state and durable domain events; workflows react to committed changes.
6. Separation of control and data planes: API services manage metadata and operations; workers process large datasets directly against object storage.
7. Storage independence: Dataset references are not tied to a single cloud or storage vendor.
8. Reproducibility: Every dataset version resolves to exact sample revisions, annotation revisions, transformation definitions, and content manifests.
9. Observable by design: All operations have correlation IDs, metrics, traces, and auditable outcomes.
10. Incremental complexity: Start with a modular monolith and independently scalable workers, rather than premature microservices.

## 3. Technology decisions

| Layer            | Technology                          | Role                                            |
| ---------------- | ----------------------------------- | ----------------------------------------------- |
| Frontend         | React, TypeScript, Vite             | Unified human interface                         |
| UI data          | TanStack Query, TanStack Table      | API caching and large tables                    |
| Visualization    | React Flow                          | Dataset and transformation lineage              |
| API              | FastAPI                             | REST, OpenAPI, events, integration endpoints    |
| Agent API        | FastMCP                             | MCP tools and resources                         |
| Contracts        | Pydantic v2                         | Domain and API validation                       |
| Persistence      | PostgreSQL                          | Canonical operational state                     |
| ORM / migrations | SQLAlchemy 2, Alembic               | Persistence and schema evolution                |
| Event delivery   | PostgreSQL outbox and inbox         | Durable at-least-once processing                |
| Orchestration    | Prefect                             | Long-running workflows and retries              |
| Processing       | DataChain                           | Unstructured data ingestion and transformations |
| Annotation       | Label Studio                        | Human labeling and review                       |
| Experimentation  | MLflow                              | Runs, metrics, model registry                   |
| Data quality     | Evidently                           | Quality evaluations, drift reports              |
| Observability    | OpenTelemetry Collector, Grafana OSS, Prometheus, Loki, Tempo               | Logs, metrics, traces, alerts                   |
| Artifacts        | S3-compatible object storage        | Source data, manifests, reports                 |
| Authentication   | OIDC/OAuth 2.0 provider             | Human and service identity                      |
| Python SDK       | Generated client plus typed helpers | Programmatic dataset consumption                |
| Deployment       | Containers and static frontend      | Portable, independently scalable components     |

PostgreSQL is the recommended initial database. CockroachDB remains a possible future alternative if distributed multi-region writes become a requirement, but it should not constrain the first implementation.

## 4. Control plane and service boundaries

The control plane should be a modular Python monolith with explicit domain boundaries. FastAPI and FastMCP are transport adapters over the same application services.

| Domain module          | Responsibilities                                                     |
| ---------------------- | -------------------------------------------------------------------- |
| Identity & Access      | Users, service principals, agents, roles, permissions                |
| Dataset Registry       | Dataset identity, discovery, metadata, ownership                     |
| Schema Registry        | Versioned Pydantic and JSON Schema contracts                         |
| Sample Registry        | Sample identity, content revisions, metadata                         |
| Annotation Management  | Label Studio integration, revisions, review status                   |
| Dataset Versioning     | Manifests, snapshots, splits, immutable publication                  |
| Lineage                | Dataset, sample, transformation, annotation, and model relationships |
| Quality                | Validation policies, Evidently reports, quality gates                |
| Operations             | Asynchronous operation tracking, cancellation, retries               |
| Workflow Integration   | Prefect deployment invocation and execution correlation              |
| Experiment Integration | MLflow run and model associations                                    |
| Events                 | Domain event persistence, outbox, inbox, subscriptions               |
| Audit                  | Actor identity, actions, changes, access history                     |

Each module should own its persistence models and expose an application-service interface. Other modules should interact through these interfaces rather than directly modifying each other's tables.

### Suggested repository structure

```
ml-platform/
├── apps/
│   ├── api/                 # FastAPI application
│   ├── mcp/                 # FastMCP interface
│   ├── web/                 # React + Vite
│   ├── workers/             # Event and integration workers
│   └── workflows/           # Prefect flows
├── packages/
│   ├── contracts/           # Pydantic domain contracts
│   ├── domain/              # Entities and business rules
│   ├── application/         # Use cases and services
│   ├── persistence/         # SQLAlchemy repositories
│   ├── integrations/
│   │   ├── datachain/
│   │   ├── label_studio/
│   │   ├── mlflow/
│   │   └── evidently/
│   ├── events/              # Event contracts and dispatch
│   ├── storage/             # Object storage abstractions
│   ├── observability/       # OTel instrumentation
│   └── sdk/                 # Python SDK
├── frontend/
│   └── generated-api/       # OpenAPI TypeScript client
├── migrations/              # Alembic migrations
├── infrastructure/
│   ├── compose/
│   ├── terraform/
│   └── deployment/
└── tests/
    ├── unit/
    ├── integration/
    ├── contracts/
    ├── end_to_end/
    └── performance/
```

For the initial implementation, these can be packages in one Python project. We should not split them into network services unless deployment or scaling requirements justify it.

## 5. Canonical domain model

The registry should distinguish between a logical dataset, its working revisions, and its published versions.

Dataset

Stable logical identity · Ownership · Description · Policy

Draft

Mutable working collection

Published Version

Immutable snapshot + manifest

Sample Revisions

Content · Hash · Metadata

Annotation Revisions

Labels · Review · Provenance

Lineage, Quality, and Experiments

Transformations · Validation · Evaluations · MLflow Runs

### Primary entities

| Entity               | Key fields                                                                      |
| -------------------- | ------------------------------------------------------------------------------- |
| `Dataset`            | ID, slug, name, owner, modality, metadata, access policy                        |
| `DatasetDraft`       | ID, dataset ID, base version, revision, status                                  |
| `DatasetVersion`     | ID, dataset ID, version, schema version, manifest digest, publication timestamp |
| `Sample`             | ID, source identity, modality                                                   |
| `SampleRevision`     | ID, sample ID, content digest, storage reference, metadata                      |
| `Annotation`         | ID, sample revision ID, task type, label schema                                 |
| `AnnotationRevision` | ID, annotation ID, revision, payload digest, reviewer                           |
| `DatasetMembership`  | Version ID, sample revision ID, annotation revision references, split           |
| `Transformation`     | ID, code version, configuration digest, environment                             |
| `LineageEdge`        | Source artifact, target artifact, relationship, operation ID                    |
| `QualityPolicy`      | ID, version, rules, severity, thresholds                                        |
| `QualityReport`      | ID, dataset version/draft, policy version, result, artifact URI                 |
| `Operation`          | ID, type, status, actor, idempotency key, external run ID                       |
| `DomainEvent`        | ID, aggregate ID, type, schema version, payload, timestamp                      |

Dataset membership must be version-specific. A sample may belong to multiple datasets and versions, potentially with different annotations or splits.

### Stable identity versus content identity

A sample's logical ID should survive metadata corrections and annotation updates. A content digest identifies the exact bytes of a particular revision.

For example:

```
sample_id: 9d5f...
revision_id: 4f83...
content_sha256: 7a91...

dataset: historical-documents
version: 2.3.0
split: evaluation
```

Changing a transcription creates a new annotation revision. Replacing the underlying image creates a new sample revision. Neither action mutates a published dataset version.

## 6. Pydantic contract architecture

Pydantic v2 is mandatory at application boundaries, but it should not be confused with database integrity or large-scale data validation.

I'd establish three contract categories:

- Domain contracts: Stable models representing datasets, samples, annotations, operations, and events.
- Integration contracts: Provider-specific input/output adapters for Label Studio, DataChain, MLflow, and Evidently.
- Storage contracts: Versioned manifest schemas, Parquet record schemas, and object references.

```
from datetime import datetimefrom enum import StrEnumfrom typing import Annotated, Literalfrom uuid import UUIDfrom pydantic import (    BaseModel,    ConfigDict,    Field,)class Contract(BaseModel):    model_config = ConfigDict(        extra="forbid",        strict=True,    )class DatasetStatus(StrEnum):    DRAFT = "draft"    VALIDATING = "validating"    VALIDATED = "validated"    PUBLISHED = "published"    DEPRECATED = "deprecated"    ARCHIVED = "archived"class ArtifactRef(Contract):    uri: str    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")    size_bytes: int = Field(ge=0)
```

The example is deliberately compact. Production contracts also need tenant or namespace scope, actor identity, access classifications, and compatibility metadata.

### Contract enforcement rules

| Boundary            | Validation                                            |
| ------------------- | ----------------------------------------------------- |
| HTTP request        | Pydantic request contract                             |
| MCP tool invocation | Pydantic tool input contract                          |
| Domain service      | Business invariants and authorization                 |
| Database write      | SQL constraints, transactions, referential integrity  |
| Event publish       | Versioned Pydantic event contract                     |
| Event consume       | Event schema validation and compatibility handling    |
| Label Studio import | Provider adapter → canonical annotation contract      |
| DataChain output    | Record schema plus bulk validation                    |
| Dataset publication | Manifest, referential, statistical, and policy checks |
| SDK response        | Generated or shared typed response contract           |

Strict Pydantic validation should be used for canonical in-process objects. External JSON input still needs intentional parsing and normalization; strict Python type validation is not identical to strict JSON validation.

### Schema evolution

Use explicit contract versions and compatibility policies.

Additive optional fields may be backward-compatible. Renaming fields, changing types, or altering annotation semantics may require a new major schema version.

Published manifests must pin their schema version. Old versions must remain readable through supported readers or explicit migration adapters.

## 7. PostgreSQL state architecture

PostgreSQL will be the authoritative platform state database.

It stores registry metadata, lifecycle state, relationships, operation state, domain events, and projections. Large sample payloads and manifests belong in object storage.

### Suggested database schemas

```
identity.*
registry.*
samples.*
annotations.*
lineage.*
quality.*
operations.*
events.*
audit.*
```

These are logical namespaces, not separate databases.

Each domain module owns its tables and migrations. Cross-module foreign keys are acceptable within the modular monolith when they protect essential integrity.

### Important database design choices

| Concern             | Approach                                                  |
| ------------------- | --------------------------------------------------------- |
| IDs                 | UUIDs, preferably time-sortable where useful              |
| Structured metadata | Typed columns for core fields                             |
| Flexible metadata   | JSONB with validated schemas                              |
| Search              | PostgreSQL full-text search and indexed filters initially |
| Concurrency         | Optim                                                     |
