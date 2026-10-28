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


## Packaged native execution and restart

The optional `infrastructure/compose/workflows.yaml` overlay ran the pinned Prefect 3.9.0 SDK/server from the workflow image, a separate persistent PostgreSQL 17.6 metadata database, native process worker, registration job, and dispatcher. Server/dashboard ports are private; dashboard is disabled and API uses generated machine authentication. Workflow credentials are generated once in ignored `.local/workflows.env`, independently of platform credentials. Existing platform data and the unrelated Label Studio services were preserved.

A newly granted synthetic qualification namespace was discovered automatically after dispatcher startup. Its admitted artifact operation reached accepted PostgreSQL success through automatic outbox/inbox/Prefect execution. Receipt: namespace `dd4a1c4a-cea4-4da4-86c3-e1c30a664539`, operation `cbb38249-18c7-4995-97b1-2006a31bd563`, sample `a68128d0-4e84-4308-9ac6-c620815d0f30`, revision `13b21bd4-5c47-484a-b70d-7c980662e479`, provider run `5b781660-60ec-467f-8203-958ce277abd1`. Accepted SHA-256 `e55d1edb90343a3ab7c4326ed0490eef623672cbca034168c04cc1ff96a2176f`, 46 bytes, logical revision 7, epoch 1; provider Completed, run count 1. No manual inbox receive/submission was used for this operation.

After restarting the packaged worker and dispatcher, PostgreSQL retained success/revision/digest and Prefect retained the same completed run with count 1. The existing platform verify-only smoke passed schema `0011_workflow_deployment`, current no-grant 403, preserved object digest, presigned read, and anonymous denial. These are actual container/network/Keycloak/PostgreSQL/SeaweedFS/Prefect checks. This restart did not kill an in-flight effect. Browser operation recovery, live adverse cancellation, independent review, and final delivery gates remain open.

The initial combined build exposed multiple services publishing the same workflow tag concurrently. The overlay now gives Prefect the sole build definition; registration/worker/dispatcher consume that image. Compose configuration validation, workflow-image build, native metadata migration, registration exit 0, worker health, and six dispatcher regression tests passed. The safe local receipt is `/tmp/verdant-issue19-packaged-receipt.json`; it contains IDs/digest only.

## Authenticated browser launch, outage, reload, reconciliation, and cancellation

Using the existing local qualification human account through the real Keycloak login, the browser opened a synthetic dataset in the packaged qualification namespace. Exact sample inspection launched operation `dad909e5-e211-4653-8065-1360566a4cc0`. Automatic dispatch produced workflow run `a9a52fde-71b5-43cd-a9d5-293bc74bfdfd`; browser polling showed succeeded, revision 7, and the expected accepted digest. `browser/operation.png` captures the accepted result. Reloading the same operation URL recovered its durable status and prior effect.

The owned API container was stopped while the authenticated operation view remained open. Polling showed an explicit connection-interrupted/stale-status warning, retained the last accepted result, and disabled recovery controls. `browser/disconnected.png` records this. API restart cleared the warning automatically; a subsequent page reload retained the same result. Session expiry occurred later during navigation, after the outage/reconnect/reload evidence, and a fresh normal Keycloak SSO login resumed qualification.

With only the packaged process worker stopped, browser inspection launched operation `b65aa6fc-c554-4032-a802-e1bbe7d95c19`, workflow run `c06b0d5b-7e0f-4a2d-932e-97426d99c7cd`. The browser requested reconciliation, then cancellation. Polling showed cancellation requested, then cancelled at revision 5. Independent PostgreSQL/provider inspection confirmed cancelled/no accepted digest while Prefect was still Scheduled with run count 0. The worker was restored afterward. `browser/cancelled.png` captures confirmed logical cancellation. This proves queued cancellation and preserves the distinction between domain status and later provider observation.

Browser inspection found the submission feedback still said waiting for cancellation confirmation after authoritative cancellation completed. Feedback now says the current status is shown above. The screenshot predates that text-only correction; 15 component tests passed afterward. Browser failed-attempt retry, read-only/revoked operation access, pagination, live adverse reconciliation, and independent review remain open gates.

## Browser failed-attempt retry and current access

A synthetic sample revision referenced an absent object. Automatic execution failed operation `5528fc8f-e33f-4137-8883-d90bae98cfe6`, epoch 1, provider run `a9439a63-cae4-47d3-8686-1a7e67677999`. Browser showed failed/reconciliation needed and the unknown `artifact.read` receipt. The fixture then conditionally created the previously absent object with exactly the registered bytes, without overwriting any object. Browser **Retry operation** admitted epoch 2. Run `71d65053-1d9a-455d-acfe-a73eaeff85cf` completed; PostgreSQL accepted SHA-256 `fe251d880e05676638c4d85fc52ef369d87749cbca9781c9d34a836afb5fa7be`, operation revision 12. The same effect receipt `dbfb5349-afc8-4283-9c7d-861eaaba82fb` advanced from unknown revision 1 to succeeded revision 2. `browser/failed.png` and `browser/retried.png` record actual browser states.

The qualification human's synthetic namespace grant was changed to read. Fresh normal Keycloak sign-in and operation navigation showed accepted status/effects with no retry/cancel/reconcile controls (`browser/read-only.png`). Revoking only that synthetic grant removed the entire operation view through current authorization polling (`browser/revoked.png`). The original qualification write grant was restored afterward. No service grant or unrelated namespace was changed.

Independent read-only review at `4a45be8` found no confirmed correctness/security blocker. It identified stale UI write permissions after a write-to-read downgrade as a nonblocking issue. Operation views now poll current namespace authorization alongside durable status, so permissions update without a failed mutation. Submission feedback also avoids saying retry is still queued after completion. Workspace production image rebuild/typecheck and 15 component tests passed. Retry screenshots predate the feedback-text correction.

Remaining qualification: operation/effect browser pagination, live adverse provider reconciliation, full final checks, final review, and PR/CI/merge lifecycle. Roadmap #1 remains open beyond #19.

## Live adverse provider reconciliation and browser pagination

With the owned process worker stopped, the dispatcher admitted operation `d99979c6-f2c3-4c3c-b602-22e85ad4c148` to real Prefect run `9c9ccbca-cddb-4d3e-be7e-edfae3d67a2a`. The qualification explicitly injected native Crashed state through Prefect's state API, with run count 0. This is a synthetic terminal-state injection against the actual server, not an actual process crash. PostgreSQL remained queued until the authenticated browser requested reconciliation. Trusted lookup verified persisted run/deployment/invocation/parameters, observed the terminal provider state, and recorded logical failed at revision 4 without an accepted result. `browser/reconciled-provider.png` records the actual outcome. The worker was restored afterward.

Twenty-six additional synthetic verification requests were immediately cancellation-requested through shared services to qualify pagination without useful work. The namespace contained 31 operations. Browser page 1 showed 25 rows; **Next operations** showed page 2 with six rows, Previous enabled and Next disabled. Selected operation detail remained present across pages (`browser/pagination.png`). Domain tests cover keyset identity completeness; a component test covers separate effect cursors and retained reconciliation status. The fixed artifact verifier accepts exactly one effect, so multiple-effect UI pagination uses a labeled component fixture rather than an invented live workload.

Final local checks: 115 workflow-enabled backend tests, 16 workspace component tests, workspace typecheck/production image build, Ruff across all 61 Python files, Alembic model/migration check, Compose base/overlay validation, and real runtime/browser checks above passed. CI now installs the pinned workflow dependency group and validates both generated-secret bootstrap paths and the overlay. CodeRabbit CLI pre-PR review failed with organization membership HTTP 403; independent review is recorded above. Final PR/CI/merge remain lifecycle gates.

## PR review recovery fixes

CodeRabbit's GitHub review of PR #49 reported three valid findings after its CLI was unavailable. Old-attempt unknown admission is now reconciled by exact lookup without reissue before acknowledging its queued event, including when a newer attempt is cancelling. Unsupported operation kinds are retained in durable consumer quarantine and acknowledged, preventing endless claim/audit churn. One namespace's denied access or ordinary dependency failure is safely logged by exception class and no longer stops delivery to other namespaces; discovery errors retry on the next loop. MCP operation limits and strict epoch inputs now match HTTP bounds.

Real PostgreSQL regressions verify an old unknown admission is reconciled and acknowledged while leaving the newer attempt authoritative, both queued and cancelling, plus unsupported-kind quarantine without repeated claims. A labeled controller double verifies one denied namespace does not stop the next and that private exception text never enters logging arguments. Type validation tests cover MCP range/strictness. Full backend suite: 120 passed; Ruff/format and whitespace checks passed. These regression claims are bounded to their labeled provider/controller doubles and real database boundary.
