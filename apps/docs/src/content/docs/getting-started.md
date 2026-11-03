---
title: Getting started
description: Run the delivered dataset workspace and understand its qualification limits.
---

Verdant has a React/Vite workspace and FastAPI/FastMCP backend with PostgreSQL,
S3-compatible artifacts and organizational Keycloak identity. The accepted target
also includes later experiment/evaluation capabilities; the complete platform is
not yet production-qualified.

## Local registry and identity

From the repository checkout, with Docker Compose v2, Python and uv installed:

```sh
python3 infrastructure/compose/bootstrap.py
docker compose config --quiet
docker compose up -d --build
uv run --locked python scripts/qualify_compose.py
```

The root composition runs the dataset registry/identity stack. Open
`http://127.0.0.1:18800` and follow the
[local provisioning and recovery runbook](https://github.com/DecisionNerd/verdant/blob/main/infrastructure/compose/README.md).
It starts without human accounts. Existing legacy digestion data is separate;
never reset or point the new stack at legacy volumes as a migration.

## First lifecycle

Create a dataset, upload/register source objects, inspect revisions, capture a frozen
candidate, validate a versioned policy and publish an immutable manifest. Exact
versions retain verified downloads and recorded lineage when the draft changes or
the dataset is archived. Clients share the same current authorization.

The workspace also supports versioned process flows and model profiles, native
Prefect/DataChain text execution and durable pause/recovery. Gemini/GLM have bounded
live qualification through OpenRouter as well as native protocol fixtures. Choose
the transport explicitly in each profile. Ordinary provider calls remain disabled
until backend credentials and a request budget are explicitly authorized.
[Live evidence and limits](https://github.com/DecisionNerd/verdant/blob/main/docs/proofs/issue-21/README.md).

Annotation uses a qualified pinned external Label Studio Community image with
individual Keycloak SSO. It retains the stock editor; paid embedding and Enterprise
SSO are not prerequisites for this qualified external contract. Configure it using
the [annotation runbook](https://github.com/DecisionNerd/verdant/blob/main/docs/engineering/ANNOTATION-TRUTH.md).
Community instances are shared-trust cohorts; project names are not access isolation.
Independent/blind access requires isolated instances, data and credentials.

## Verification and scope

Automated browser tests exercise actual Keycloak, native Prefect/DataChain and the
qualified stock labeling editor. PostgreSQL/S3 tests cover integrity, denial,
idempotency, retained versions, recovery, leakage and input isolation. Exact source,
image, environment and fixture/live limits are retained in the
[proof tree](https://github.com/DecisionNerd/verdant/tree/main/docs/proofs).
These are bounded local qualifications, not a cloud capacity or full-cluster claim.

`pnpm --filter @verdant/docs dev` previews these operator docs;
`pnpm --filter @verdant/docs build` builds the static site only.
