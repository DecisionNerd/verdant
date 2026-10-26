# Platform requirements

These are target acceptance conditions, not a report of passing implementation. The baseline is [the supplied reference](engineering/REFERENCE-BASELINE.md); concurrency, recovery, and security scenarios below are derived requirements that make its invariants testable.

| ID | Requirement | Acceptance evidence |
|---|---|---|
| FR-01 | Dataset-first registry | Create/discover an owned dataset with no model, experiment, or labeling project. |
| FR-02 | Shared services | Equivalent REST and MCP requests produce the same authorized result; SDK follows OpenAPI contracts. |
| FR-03 | Revision identity | Correcting labels adds an annotation revision; replacing bytes adds a sample revision; stable logical sample identity survives. |
| FR-04 | Version membership | A sample can occur in multiple datasets/versions with distinct pinned annotations and splits. |
| FR-05 | Immutable publication | Pin sample metadata, annotation/task/label-schema revisions, review evidence, splits, transformation code/configuration/environment, and publication gate evidence; later edits leave historical interpretation and bytes unchanged. |
| FR-06 | Schema validation | Invalid external inputs, provider payloads, event versions, or manifests are rejected or explicitly quarantined; SQL constraints protect relational integrity. |
| FR-07 | Async operations | A long-running request returns an operation; status/events persist across API/worker restarts; retries and cancellation have auditable outcomes. |
| FR-08 | Durable events | State/event/outbox commit atomically; duplicate delivery has one logical consumer effect; failed delivery remains recoverable. |
| FR-09 | Prefect/DataChain execution | An ingestion/transformation records exact inputs, configuration/code/environment, outputs, lineage, and operation correlation. |
| FR-10 | Annotation review | Label Studio results normalize to canonical revisions pinned to exact sample/schema identities; duplicate or stale webhooks cannot replace newer accepted state. |
| FR-11 | Quality gates | Evidently reports pin evaluated content and policy versions; a failed gate prevents publication of that candidate. |
| FR-12 | Experiments | MLflow runs/models reference exact dataset versions; changing MLflow metadata cannot mutate registry identity. |
| FR-13 | Authorized access | Human, service, and agent identities are checked at every service boundary, including discovery, events, lineage, and artifact access. |
| FR-14 | Reproducible consumption | SDK/client verifies a pinned manifest and content digests; unavailable or corrupted artifacts fail explicitly. |
| FR-15 | Audit and telemetry | An operation is traceable from transport through committed state, delivery, Prefect, processing, and integration result; audit survives telemetry retention. |
| FR-16 | Execution authority | Operations owns terminal state; Prefect retries tasks within one epoch. Stable logical effect receipts survive attempt-epoch changes; unknown dispatch stops for reconciliation; duplicate or stale results cannot duplicate external effects or reverse committed outcomes. |
| FR-17 | Domain boundary | Trusted Python workers and remote API workers execute the same authorized domain commands; direct persistence access goes through the application modules and cannot bypass version integrity. Independent integration calls need no Prefect run. |
| FR-18 | Revocation | Commands/batches and result/finalization check applicable current access without per-record identity-provider calls. Revoked principals cannot register results; storage credential/URL expiry and revocation behavior are documented. |
| FR-19 | Rebuildable reads | Where asynchronous projections are used, interrupted rebuilds resume without losing authoritative data. Stale search cannot authorize access or publish; SSE disconnect falls back to durable status. |
| FR-20 | Availability lifecycle | Deprecation/archive preserve authorized pinned reads and bytes; unarchive restores visibility. Archived datasets pause new work/publication without losing admitted work checkpoints. Deletion is separate. |
| FR-21 | Graceful degradation | Telemetry, optional integration or projection outages do not block authoritative reads or work independent of the failed component; required gates still cannot report false success. |
| FR-22 | Concurrent editing/publication | Publish an explicit frozen candidate while the draft advances; only explicit current-draft publication requires equality at finalization. Show both revisions and reuse valid preparation after conflicts. |
| FR-23 | Retention promotion | Preparation durably protects and verifies the complete generation; finalization promotes one retention group atomically with publication. Expiry/collection cannot race it into dangling references. |
| FR-24 | Organizational cluster SSO | One Keycloak login enters the frontend and every enabled dashboard as the correct individual, with no second password; service clients use noninteractive authenticated paths. Each dashboard's edition/plugin, roles, provisioning, logout/revocation and bypass prevention are verified. Unqualified optional profiles remain unavailable; no SaaS identity dependency is required. |
| FR-25 | Portable object storage | SeaweedFS OSS is the local S3 default. Qualify Verdant and enabled MLflow/Mimir/Loki/Tempo clients against each backend; placement/signing configuration may change without changing logical artifact/version identity or immutable manifest bytes. Existing data cutover copies/verifies retained objects with a tested rollback boundary. |

## Cross-cutting proof

| ID | Constraint | Acceptance evidence |
|---|---|---|
| NFR-01 | Modular Python monolith | FastAPI and FastMCP delegate to shared services; modules do not write each other's tables directly. |
| NFR-02 | Control/data plane separation | API handles bounded metadata; workers stream/batch bulk objects through S3-compatible storage. |
| NFR-03 | Concurrency safety | Competing draft updates reject stale revisions; concurrent publication/idempotency requests cannot create divergent snapshots. A sealed membership generation is immutable; finalization scans/copies no membership and performs no external calls. |
| NFR-04 | Bounded resource use | Representative large datasets prove pagination, bounded memory, backpressure, and cancellation; API/worker pool and concurrency budgets preserve interactive capacity during bulk work and retry storms; deployment budgets are measured before release. |
| NFR-05 | Recovery and retention | Restore PostgreSQL and object storage together and resolve published manifests; no garbage collection removes retained referenced bytes. |
| NFR-06 | Compatibility | Old published manifests remain readable through supported readers/adapters; breaking contracts have explicit versions. |
| NFR-07 | Accessibility | Keyboard, focus, contrast, semantic status, and lineage text alternatives pass the design contract. |
| NFR-08 | Portable deployment | Static frontend and containerized backend/workers use externalized configuration and narrowly scoped credentials. Docker Compose and Kubernetes prove the same internal organizational lifecycle with self-hosted Keycloak, persistent state, restart recovery and documented bootstrap/start sequences. YAML validation alone is insufficient. |

## First end-to-end acceptance

Given an authenticated dataset owner and an uploaded source object, when the owner creates a dataset, ingests a sample into a draft, validates it, and publishes, then a new immutable manifest resolves the exact bytes, quality result, and provenance. A second authorized client retrieves the same manifest; an unauthorized principal cannot discover or read it. Repeating delivery or the publication request creates no second logical result.

See [Testing](engineering/TESTING.md) for proof layers and [Implementation](engineering/IMPLEMENTATION.md) for sequencing.
