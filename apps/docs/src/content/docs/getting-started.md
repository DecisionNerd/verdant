---
title: Getting started
description: Readiness and the first dataset lifecycle.
---

## Readiness

These docs describe the accepted target architecture. The React/Vite + FastAPI/FastMCP dataset platform is not declared implemented by this documentation change. Existing Compose, CLI and web commands may still run the earlier document-digestion tool; they are not a platform installation recipe.

## Target prerequisites

A deployment will need an OIDC/OAuth 2.0 provider, PostgreSQL, S3-compatible storage, the Python control plane/workers, Prefect/DataChain execution, the static React/Vite frontend, and OpenTelemetry with Grafana OSS, Mimir, Loki and Tempo. Label Studio, MLflow and Evidently integrations are introduced as their lifecycle slices pass acceptance. Supported versions, provider topology and environment-variable names must be established with implementation.

## First lifecycle

Create a dataset → register source objects → ingest into a draft → inspect sample revisions → validate against a policy → publish an immutable manifest → consume that exact version from another authorized client. Attach annotations and experiments to dataset versions rather than creating dataset identity inside those systems.

Before availability is claimed, verify digest integrity, unauthorized denial, repeated idempotent requests, worker restart/recovery and unchanged old versions after edits. The repository's `docs/engineering/IMPLEMENTATION.md` defines the delivery plan.

## Preview these docs

With the repository's pnpm dependencies installed, run `pnpm --filter @verdant/docs dev` from the repo root. The Astro development server reports its address. `pnpm --filter @verdant/docs build` verifies the static site. These are documentation commands only.

## Local deployment target

Docker Compose and Kubernetes are self-hosted internal organizational targets with Keycloak as the common identity authority. The root Compose file currently runs the earlier digestion stack, so target-platform local availability is not yet established. Repository `docs/infra/docker-compose.md` defines bootstrap, persistent dependencies, real login and end-to-end restart/recovery acceptance; `docs/infra/local-auth.md` defines the canonical browser/container issuer and client setup. No SaaS identity provider is required. Full dashboard SSO still requires compatible self-hosted editions/plugins: Label Studio currently has a documented Enterprise SSO prerequisite; Grafana OSS uses built-in Keycloak OAuth without a paid SSO dependency. The core can run with unqualified optional dashboards disabled; that is not full-cluster SSO.
