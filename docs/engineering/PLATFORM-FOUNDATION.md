# Python registry foundation

Issue #10 supplies a bounded PostgreSQL registry backend. The full platform, browser UI, Keycloak verification, object ingestion, frozen publication and provider integrations remain separate acceptance slices. Legacy TypeScript runtime and root Compose are not this backend's deployment.

## Run locally

Use Python 3.12 and uv 0.8.16. Supply an independently managed PostgreSQL database and credentials through the environment:

```sh
uv sync --frozen
export VERDANT_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST/DATABASE'
uv run alembic upgrade head
uv run uvicorn verdant.api:create_app --factory
```

Run migrations once before serving traffic. The initial migration requires permission to install PostgreSQL's `btree_gist` extension. Startup neither creates nor resets schema. Back up before migration; downgrade drops registry data and is not a production rollback procedure. Keep database credentials outside source and logs.

`/health/live` checks process availability. `/health/ready` checks database/schema compatibility and reports contract, event and reserved manifest versions. Its `identity: unqualified` flag means authentication has not passed acceptance. All registry HTTP routes return 401 until issue #11 installs verified identity. No header or shared-password bypass is supplied. OpenAPI is at `/docs`.

## Shared contracts and ownership

`src/verdant/contracts.py` defines Pydantic v2 inputs and results. `application.py` owns authorization and one transaction per use case; `persistence.py` owns SQLAlchemy mappings and scoped queries. `domain.py` imports only the standard library. Managed callers supply a trusted `Principal`; readable/writable namespace grants must originate from verified identity, never request payloads. Namespace provisioning and grant administration are not public endpoints in this slice.

Dataset and sample IDs are stable UUIDs. Sample content or metadata changes append a revision; old revision rows are immutable. Object keys, digests and sizes are declarations here: this backend does not upload bytes or verify storage content. Dataset drafts start at revision zero. Membership writes require `expected_revision`, lock the draft and return 409 on stale state. Effective changes atomically record actor, namespace, affected identity and audit event; unchanged writes retain the revision.

Membership reads support a historical `revision`, UUID `after` cursor and bounded `limit`. **Continue every page with both the returned `revision` and `next_after`.** Omitting revision reads the latest draft and can mix states across pages. Historical membership remains readable after subsequent edits. Draft history is preserved through the authorized service path; database triggers constrain row rewrites but do not defend against an administrator issuing arbitrary backdated SQL. Namespace composite foreign keys prevent cross-namespace references. This is not sealed publication.

Errors use 401 for unverified identity, 403 for namespace denial, 404 for missing scoped entities, 409 for stale/duplicate commands and 422 for invalid contracts. Unknown database integrity failures are not disguised as expected conflicts.

## Telemetry

Shared use-case boundaries emit OpenTelemetry spans and latency histograms, including authorization and commit failures. Existing caller trace context is retained. Metric labels contain only operation and outcome; spans omit command payloads, exception messages, actor and namespace. Durable audit retains actor/namespace attribution. Optional signal failures cannot abort a use case. The OTel API defaults to no-op until the process composition installs a provider; SDK/exporter/Collector wiring and live backend delivery are deployment qualification work, not a claim made by this foundation. Configure asynchronous bounded exporters in that deployment, never a synchronous network exporter on the command path.

## Proof

Use an isolated PostgreSQL database whose name ends in `_test`. Tests migrate that database, create fresh namespace fixtures, and retain rows; never point them at production.

```sh
export VERDANT_TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST/verdant_test'
uv run pytest -q
uv run ruff check src migrations tests
uv run ruff format --check src migrations tests
VERDANT_DATABASE_URL="$VERDANT_TEST_DATABASE_URL" uv run alembic check
```

The suite covers real PostgreSQL bootstrap/repeat upgrade, contract rejection, stable sample revisions, historical membership, stale/concurrent writes, atomic audit rollback, database constraints, authorization parity and fail-closed HTTP identity. CI uses Python 3.12, PostgreSQL 17.6 and the committed uv lock. Test-only dependency overrides exercise HTTP composition; they are not evidence of live Keycloak or browser authentication. No claim of production deployment, object durability or full-roadmap completion follows from these tests.
