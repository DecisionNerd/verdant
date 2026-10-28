# Durable operation verification preflight

Issue #19 remains in implementation. These receipts prove the shared-service and artifact-read path, not Prefect admission, worker deployment or operation UI.

## Evidence

- Real PostgreSQL tests cover atomic admission/audit/event/outbox, competing bounded claims, expired claim replacement, stale acknowledgements, revoked authorization, retry epochs, cancellation races, stable logical effects, ambiguous-write reconciliation, accepted-result finalization, inbox deduplication, monotonic checkpoints and quarantine.
- `tests/test_verification.py` uses real PostgreSQL and a controlled byte-stream storage double. It proves bounded-chunk verification, reuse of accepted success, corruption failure/retry, and process death after read but before receipt completion. The crash injection is a test-only BaseException; it is not a production crash switch.
- `scripts/qualify_verification.py` used the pinned local SeaweedFS fixture on loopback 18333 and isolated `verdant_issue19_test`. It created a unique synthetic object and service identity, rejected intentionally corrupt bytes, repaired that fixture, explicitly retried, reconciled the read and committed success at epoch 2. Repeating the completed operation reused its receipt. No pre-existing object was overwritten.

Real receipt: operation `d4798459-67d1-4be0-8898-0bf38c19a065`, namespace `4831fab6-68b3-491f-b53d-55c2da8e50ed`, SHA-256 `a8ec243fc95c4477933c950b8cf86cea00b5f5cec5506c714912404c6f77a5c7`, 44 bytes, epoch 2, status succeeded. Synthetic object and database records were retained for recovery checks. Keys/credentials are not printed by the qualifier.

## Actual capability and boundary

The runner streams at most 1 MiB per read, rejects excess size, checks exact byte count and SHA-256, and checks operation state during bounded read batches. Artifact reads have no external write effect: repeating a read is the explicitly supported reconciliation protocol. That rule must not be generalized to provider writes. Unknown writes remain blocked until authorized found/absent reconciliation.

Success finalization checks the exact registered sample revision and one matching accepted artifact-read receipt, then commits its result, terminal state, audit, event and outbox together. External workflow success alone is insufficient. Cancellation/finalization and stale epochs remain guarded by PostgreSQL locks/current grants.

Pending #19 work: actual workflow provider submission/reconciliation, process worker commands and lifecycle proof, REST/MCP operation controls, authoritative UI status/reconnect/retry/cancel/reconcile, observability/runbook and final review/CI/merge.

## Native Prefect admission and lost-response recovery

Prefect 3.9.0 ran locally on loopback `127.0.0.1:18420`, using dedicated PostgreSQL database `verdant_issue19_prefect`, with server analytics disabled. `scripts/qualify_prefect_submission.py` targets only that fixed local URL and an isolated Verdant `*_test` database.

The real server accepted a deployment run, then the qualification wrapper simulated a lost response. PostgreSQL retained dispatch status unknown. A new adapter call queried Prefect by exact idempotency key, verified deployment/parameters, recorded the existing provider reference, and reused it on another call. Exactly one provider run existed.

Receipt: operation `a558ddf0-c047-4b05-8eaa-7daf4200624a`, deployment `ee069945-0fbd-4b34-96c5-f4812dfe7245`, provider run `2667d0fe-1824-40ac-97ee-bd06bebb355d`, dispatch submitted, provider count 1. The response-loss injection is qualifier-only. No flow execution is claimed for this admission-only deployment.

Deployment ID is bound into the persisted dispatch request digest before first submission. A changed deployment cannot silently reuse that request. Native Prefect idempotency protects the create call; unknown outcomes still take the explicit lookup/reconciliation path before reissue. The workflow SDK is an optional pinned dependency group, separate from API runtime installation.

## Real process flow and worker restart/replay

The native Prefect process worker ran `verdant-artifact-verification` from `src/verdant/flows.py`. It fetched and validated a Keycloak client-credentials token through the existing verifier, resolved an explicitly provisioned service identity/current grants, checked its deployment and invocation identity, streamed the actual SeaweedFS object, and finalized through shared PostgreSQL services.

Receipt: operation `c5b0ba48-bda6-4944-817c-c727e6926c43`, namespace `bd899dcb-c909-4fa0-953c-d3f90f1e1520`, service principal `fb043300-9e52-4d62-8bfe-75a1a84812f0`, deployment `a8767514-e50a-44f4-9f15-ed1d6f65274d`, provider run `b2b5bbc1-4ac5-4deb-bf4d-f22914b75628`. Prefect reported Completed; independently queried PostgreSQL reported succeeded and accepted SHA-256 `773000759aec7b1d96ff55695d21f713515f6ad6811f12bf759352f35d97d8f3`, 43 bytes, epoch 1. Principal kind was independently checked as service. Worker health at loopback 18421 returned OK.

The idle owned worker received SIGINT and exited 0. A new process worker started against the same pool. The qualification explicitly rescheduled the same disposable provider run using Prefect's native state API. It completed again with provider run_count 2. PostgreSQL operation revision was unchanged from accepted success, and the accepted result/digest remained intact. This is at-least-once replay after worker restart, not proof of killing a process mid-write. Separate real-database crash/ambiguity tests cover that boundary with labeled provider doubles.

Commands: set local `PREFECT_API_URL=http://127.0.0.1:18420/api` and an isolated `VERDANT_TEST_DATABASE_URL`, then `uv run --group workflows python scripts/qualify_prefect_execution.py` prepares/submits the fixture; the same command with `--worker` starts the process worker. The qualifier reads private local Compose inputs without printing them and only targets the declared loopback/test services. Native local qualification uses the dedicated Prefect PostgreSQL database; no cloud/model calls occurred. This host process proof does not yet qualify a packaged Compose worker or the operation UI.


## Authorized operation read and control boundary

REST and MCP expose fixed artifact-verification admission, bounded namespace operation pages, bounded effect summary pages, and epoch-fenced retry/cancel requests. Admission validates the sample revision in the authorized transaction. Detail responses omit effect requests, private object keys, and provider payloads. No public endpoint accepts completion or reconciliation facts from a client.

Real PostgreSQL regression tests verify revoked readers cannot read either existing or missing operations, read-only users cannot cancel, operation/effect pagination remains bounded, unknown effects remain visible across pages, and missing verification revisions create no operation. On 2026-10-27, the complete workflow-enabled backend suite passed 107 tests; Ruff and diff whitespace checks passed. These are domain/transport wiring checks, not browser or automatic-dispatch proof. Automatic dispatch, trusted reconciliation controls, packaged workflow services, and operation UI remain incomplete.

## Bounded automatic workflow delivery

`uv run --group workflows python -m verdant.dispatch --namespace UUID --deployment UUID` runs the dispatcher against explicit authorized namespaces. It uses the same verified Keycloak service identity as the process worker and native Prefect client configuration. Each tick claims at most 25 deliveries, consumes the durable inbox, submits the supported artifact-verification operation, and acknowledges only after submission succeeds. SIGINT/SIGTERM stop between bounded work batches. Unsupported operation kinds remain deferred rather than running under the verification deployment.

Provider failure or an expired claim leaves delivery unacknowledged. Repeated inbox consumption returns the persisted dispatch. Unknown admission takes the existing exact invocation-key lookup before any reissue. Unknown cancelled/old attempts remain deferred pending trusted reconciliation; this dispatcher does not claim that cancellation recovery is complete.

Real PostgreSQL dispatcher tests use a labeled provider double: create succeeds then loses its response, the fixture lease expires, redelivery reconciles the same provider run, and subsequent delivery never creates another run. Revoked namespace access denies claims before provider calls. The full workflow-enabled suite passed 108 tests before the additional revocation test; both dispatcher tests passed afterward. Ruff checks passed. This is not yet packaged-runtime or browser qualification.

## Read-only cancellation recovery

The dispatcher handles cancellation requests even when cancellation precedes workflow inbox admission. For an uncertain admission, it performs exact provider lookup with creation disabled. Found provider identity is recorded; proven absence returns the dispatch to pending without creating a run. Only then can the fixed artifact-verification operation cancel.

`cancel_verification` locks the operation, verifies its operation kind and exact artifact-read effect identity, reconciles an abandoned read as absent, and atomically records cancellation/audit/events. This is safe because the supported effect only reads immutable data. It does not relax generic write-effect cancellation. Concurrent or delayed workers cannot finalize a cancelled operation. It does not claim to terminate an already executing Prefect process; native provider observation may finish later while PostgreSQL retains the cancelled logical outcome.

Real PostgreSQL tests cover cancellation before dispatch, uncertain admission with a found run, uncertain admission with proven absence and no create, and an abandoned read followed by rejected finalization. Provider lookup uses a labeled double. The full workflow-enabled suite passed 112 tests before adding the absent-provider variant; targeted operation/dispatcher tests passed afterward. Ruff checks passed. Live provider cancellation and browser evidence remain required.

## Durable user-requested reconciliation

REST `POST /api/v1/namespaces/{namespace_id}/operations/{operation_id}/reconcile` and MCP `operation_reconcile` accept only the current epoch. The shared service checks current write access, rejects unsupported or terminal operations, and atomically emits a durable reconciliation request with audit/outbox. Repeated requests deduplicate while that epoch's request remains undelivered. Clients cannot supply provider references, results, or claims of success.

The dispatcher performs trusted exact admission lookup, then observes the persisted provider run with deployment, invocation key, parameters, and run ID checks. A final provider state without a committed domain result fails the logical attempt safely; explicit retry can reconcile the immutable artifact read. Provider completion never creates an accepted verification result. No admission is created by the reconciliation path. Quarantined events are acknowledged without executing operation actions.

Real PostgreSQL tests cover durable request deduplication, current read-only denial, stale epochs, terminal rejection, and provider Completed without domain acceptance becoming failed rather than succeeded. Provider observation is a labeled double. Full workflow-enabled backend suite: 115 passed. Ruff checks passed. UI controls and live provider/browser qualification remain incomplete.

## Operation workspace implementation

Workspace navigation now includes operation list/detail, bounded operation/effect pagination, polling/reconnect status, queued/running/cancellation requested/cancelled/failed/succeeded labels, reconciliation-needed indication, prior effects, accepted digest, and write-authorized retry/cancel/reconcile requests. Exact sample inspection can admit verification with a stable per-inspection request identity and link to durable operation status.

Component regression tests cover epoch-only reconciliation requests, stale status with disabled actions, reconnect, revoked access hiding details, read-only controls, and admission response-loss request identity reuse. Workspace typecheck and production build passed; 15 component tests passed. These remain component/build evidence; real authenticated browser operation proof and packaged workflow deployment remain required.
