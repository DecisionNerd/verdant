# Implementation slices

The architecture is accepted; this is a target delivery plan, independent of existing engineering. No directory scaffold, runtime migration, provider choice, or deployment is executed by this docs change.

## Qualification workload

Keep the platform modality-neutral. Choose a real representative dataset for each supported processing adapter; record modality, count, byte/size distribution, annotation density, edit concurrency, execution cost and peak memory. Images/PDFs are a convenient initial profile from this repository, not a platform requirement or a gate on other modalities.

Start correctness tests small, then scale metadata membership independently from bulk-processing tests. Example scale points such as 10,000/100,000/1,000,000 rows are optional benchmark inputs, not release requirements, runtime limits or capacity claims. Use actual demand to select test sizes. Validate each admitted modality's parsing, bulk schema, memory and retry behavior; a new modality that meets existing contracts needs no architecture redesign.

Record candidate-build/validation time, finalization lock duration, memory, object traffic and queue delay against an explicit expected workload. Choose the simplest streaming manifest and indexed SQL reads that meet those measurements. Add sharding/projections only when evidence warrants them. Establish deployment budgets before production promotion; do not hold the first useful lifecycle hostage to an invented corpus size.

## Staged tracer bullet — one reproducible dataset version

These are sequential feature milestones (`issues`) within the chosen architecture; a milestone does not claim the whole platform is available.

1. **Registry and publication core:** one Python project, Pydantic contracts, shared authorized services, SQLAlchemy/Alembic, PostgreSQL and S3-compatible storage. Use an authenticated REST client with a preexisting verified object. Create a draft, stage/seal membership, validate an explicit policy and publish through a short guarded finalization. Prove unchanged snapshot interpretation after every upstream metadata/schema/review edit, plus explicit current-draft conflicts, frozen-candidate publication during edits, aggregate retention promotion and collector races. Exercise membership growth appropriate to the initial workload; record the tested envelope.
2. **Durable execution:** add operation/outbox dispatch and one Prefect/DataChain ingestion flow. Tasks receive bounded work specs and invoke shared domain services directly in managed Python deployments or through FastAPI across a remote boundary; use the same authorization and persistence rules. Prove uncertain-dispatch reconciliation, task retries, stale epochs, cancellation races, reuse of completed/unknown effect receipts across epochs, duplicate external effects and permission revocation. Record supported provider deduplication/lookup semantics rather than assuming them.
3. **Human and agent parity:** add the minimal React/Vite workspace, generated client/helpers and FastMCP adapter against already proven use cases. Prove equivalent authorization/idempotency outcomes, durable progress recovery, digest verification and keyboard-accessible publication. Contract design precedes adapters; adapters need not all ship in milestone 1.
4. **Operational acceptance:** prove the [local Compose lifecycle](../infra/docker-compose.md) with real Keycloak login, persistent dependencies, restart recovery and recorded source/image versions; correlate the complete lifecycle in Grafana OSS, verify durable audit and coordinated restore, record observed budgets and source versions, and prove API responsiveness under background saturation with separate pool/concurrency budgets. Run FR-01–09 and FR-13–23 plus all applicable NFRs. Only then claim this integrated lifecycle available.

OTel instrumentation accompanies each milestone; deploying the full Grafana OSS experience is not a prerequisite to the first registry proof. Do not scaffold Label Studio/MLflow/Evidently runtime services before their acceptance slices. All selected technologies remain part of the target; activation is staged to shorten the feedback loop.

## Deepen the lifecycle

| Slice | Classification | Exit proof |
|---|---|---|
| Label Studio import/review | Feature work | Exact revision/schema mapping, verified/deduplicated callbacks, immutable old versions |
| Evidently quality and drift | Feature work | Pinned policy/content reports, publication gate, new report per reevaluation |
| MLflow associations | Feature work | Runs/models resolve exact dataset versions without owning dataset state |
| Generated SDK and UI breadth | Feature work | Compatibility, pagination, lineage, accessible operations and cancellation |
| Operational hardening | Feature work | Bounded workloads, restore drills, replay/reconciliation, numeric SLOs |
| Retire legacy digestion runtime | Separate migration work | Explicit inventory, retention/export plan, dry run, accepted parity and rollback evidence |

`refactor-it` applies only when moving proven behavior behind an existing service boundary without changing its contract. The platform redesign itself is feature work. Historical documents or fixture data must not be deleted as an implicit implementation step.

## Open questions for research-it

- Keycloak organizational realm/client bootstrap and browser/container/cluster issuer routing; stable principal mapping, backend-owned browser sessions and service tokens; qualify each enabled dashboard edition/plugin against the cluster SSO matrix. Dataset/project namespaces organize internal authorization; public SaaS tenancy and provider-swapping are outside this target.
- Exact endpoint shapes, canonical manifest v1 encoding/order test vectors and supported schema readers. Snapshot contents, staged generation publication and idempotency conflict semantics are decided in Architecture. A streaming single manifest is the default; sharding is workload-driven.
- Qualify the pinned Prefect/provider versions for stable dispatch keys and reconciliation; implement the decided Operations/Prefect authority and epoch fencing contract.
- DataChain record schema and scale limits for representative modalities; quality policy thresholds and drift references.
- Qualify the external Label Studio image/API contract, contributor identity, shared-trust deployment boundary, webhook authentication and review semantics; MLflow/Evidently supported versions and compatibility. Label Studio authentication implementation is maintained in its own repository.
- Qualify SeaweedFS OSS single-node local S3 plus each enabled cloud backend using the common and integration-client contract suite; pin image/flags/persistence, digest/signing compatibility, retention/garbage collection and consistent backup/cutover procedures. SeaweedFS is the local default; cloud selection remains deployment-specific.
- Hosting topology and trust boundaries, service versions, secrets management, CORS/CSRF/session design, resource budgets, SLOs and alert thresholds. Prefer direct Python services within one managed trust boundary; introduce network isolation only when useful.

These questions refine adapters and operations. They do not reopen the chosen React/Vite + FastAPI + FastMCP + PostgreSQL architecture.
