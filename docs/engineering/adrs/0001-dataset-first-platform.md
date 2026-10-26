# ADR-0001: Dataset-first platform baseline

- **Status:** Accepted (target architecture)
- **Date:** 2026-10-25
- **Decider:** Repository owner

## Context

Dataset identity and reproducibility must survive changes in annotation projects, models and experiments.

## Decision

Use React/TypeScript/Vite with TanStack Query/Table and React Flow; FastAPI and FastMCP; Pydantic v2; PostgreSQL with SQLAlchemy 2/Alembic; S3-compatible artifacts; Prefect and DataChain; independent Label Studio, MLflow and Evidently adapters; self-hosted Keycloak OIDC/OAuth 2.0; OpenTelemetry with Grafana OSS, Mimir, Loki and Tempo. Start with a modular Python monolith and independently scalable workers. PostgreSQL search is the initial discovery mechanism. Add services, projections, manifest sharding or multi-region database writes only for demonstrated needs.

## Consequences and verification

Datasets exist without models or annotation projects. Select hosting, supported versions and workload budgets through implementation qualification. Logical package boundaries need not become separate projects or network services.

See [Architecture](../ARCHITECTURE.md), [Implementation](../IMPLEMENTATION.md) and [Testing](../TESTING.md) for the complete contract and proof plan.
