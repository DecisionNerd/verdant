# Verification strategy

This is the target proof plan. Existing digestion tests do not prove dataset-platform readiness. [Requirements](../REQUIREMENTS.md) are the acceptance ledger; record exact source/version, environment, dataset size, results and limitations with each implementation slice.

| Layer | Required proof |
|---|---|
| Unit | Entity lifecycle, stable vs content identity, revision selection, policy rules, contract normalization |
| Contracts | OpenAPI/SDK/MCP parity; event and manifest compatibility; invalid and unsupported versions |
| PostgreSQL integration | Real constraints/transactions, atomic state+event+outbox, inbox deduplication, stale revision rejection, concurrent publication |
| Artifact integration | Verified uploads/reads, deterministic manifest digest, missing/corrupt bytes, staged orphan reconciliation |
| Worker integration | Duplicate/out-of-order delivery, claim loss, crash/restart, unknown external dispatch outcome, retry and cancellation |
| Provider adapters | Prefect correlation/reconciliation, DataChain bulk schema, Label Studio callbacks/review, MLflow association, Evidently report/gate |
| Security | Identity verification, scope/namespace denial on REST/MCP/SDK, discovery/count/lineage/artifact isolation, callback replay, ingestion SSRF |
| End to end | Create → ingest → annotate → validate → publish → consume → evaluate; old versions unchanged after corrections |
| Performance | Representative scale; bounded memory, streaming/pagination, queue pressure, API latency and cancellation responsiveness |
| Recovery | Coordinated database/artifact restore; retained version resolution and digests; migration rollback compatibility |
| Accessibility | Keyboard/focus/dialog/status, contrast, reduced motion, text lineage alternative |

## Adversarial publication cases

Advance the draft during candidate validation/finalization and publish the original candidate successfully; separately test an explicit current-draft conflict; race two publishers with the same version label; repeat the same idempotency key with identical and conflicting inputs. Crash after object upload but before database commit, after commit before event delivery, and after external dispatch before receipt recording. Assert one logical result, recoverable failure, no published pointer to unverified bytes, and no mutation of an existing snapshot.

## Documentation proof

Build Starlight using `pnpm --filter @verdant/docs build`; check local links, ADR index, sidebar routes, and obsolete active-stack claims. Preserve the supplied reference's limitations and fixture Markdown. Documentation builds establish content integrity only, not runtime acceptance.

## Resolution acceptance matrix

| Concern | Required falsification case |
|---|---|
| Snapshot interpretation | Change sample metadata without changing bytes; change label schema, review decision, transformation definition and quality policy. Old manifest and its resolved interpretation remain identical. |
| Sealed membership | Concurrent edits cannot produce a mixed candidate; inserts/updates/deletes on sealed generations are refused, including accidental direct persistence writes. |
| Bounded finalization | At representative and increasing membership sizes, inspect query plans/instrumentation for no membership scan/copy during finalization. Measure lock time and peak memory; report rather than invent budgets. |
| Collection safety | Race candidate lease expiry, collector claims and finalizer commit; no retained/published reference can point to a collected object. |
| Execution authority | Provider accepts dispatch then times out; duplicate task runs; stale epoch completion; flow succeeds without results; flow fails after committed success. Operations remains authoritative. |
| External effects | Provider lacks idempotency/lookup: ambiguous writes stop for reconciliation, never blind replay. Retried artifact writes verify existing bytes. |
| Cancellation | Race cancellation and finalization on one guard; only the permitted terminal outcome commits, with prior effects recorded. |
| Authorization | Revoke a user's grant or service identity during work; deny unauthorized later commands/batches and result registration; document storage expiry/revocation limits without requiring per-record remote checks. |
| Boundaries | Import checks reject domain→ORM/provider/transport dependencies; managed Python and remote API workers have equivalent command outcomes; all domain mutations traverse authorized services/persistence guards and independent integrations work without Prefect. |
| Projection recovery | Interrupt a rebuild and delivery; resume/catch up without missed effects, preserve existing reads, and deny access based on current policy despite stale projected rows/counts. |
| Subscriptions | SSE reconnect with an expired cursor refreshes authoritative state; a slow optional webhook subscriber does not block commits. |
| Availability lifecycle | Deprecate, archive and unarchive versions/datasets; verify default discovery, explicit lookup, admitted work, publication pause and unchanged digests. No lifecycle flag deletes data. |
| Graceful degradation | Disable Grafana OSS, disconnect SSE, pause a search projector or one optional integration; unaffected authoritative operations keep working and required failures stay explicit. |

Use workload profiles from [Implementation](IMPLEMENTATION.md); this documentation change claims no executed runtime qualification.

## Retry, publication and saturation proof

- Complete an external effect, fail before workflow result registration, advance the attempt epoch and retry. Reuse the original receipt/provider reference; create no duplicate external task or charge. Test the same recovery with unknown receipts, changed inputs and stale workers.
- Capture revision 42, edit through revision 45 during batch preparation and validation, then publish candidate 42 with identical membership/digests. Explicit current-draft publication conflicts instead and preserves its reusable candidate.
- Prepare a complete protected generation; race finalization against group expiry/release and collector claims. Only promotion or release wins. An expired group must reestablish protection before readiness. Inspect finalization queries to prove there are no per-artifact checks/reference rewrites.
- Saturate background connection/concurrency budgets with ingestion, rebuilding and jittered retries. Verify interactive reads retain their measured budget, transactions hold no connections during external waits, queues remain bounded and crashed batches resume without duplicating logical effects.

## Organizational cluster identity proof

Apply the same [Keycloak service matrix](../infra/local-auth.md) to Compose and Kubernetes. Sign in once through the frontend, enter every enabled dashboard without another password, and verify the actual user/role plus Label Studio annotator attribution. Qualify the selected edition/license/plugin rather than counting an upstream proxy gate as downstream SSO. Test browser session/logout, service-token paths without redirects, denied users, wrong audiences, direct-route bypass and signing-key/restart recovery. Record session/revocation limits for each component. A healthy identity container or shared admin login is insufficient proof.

## Object-backend parity

Run the [local/cloud S3 contract matrix](../infra/local-object-storage.md) against pinned SeaweedFS and each enabled destination, with actual Verdant/MLflow/Mimir/Loki/Tempo clients. Verify host/container presigned access, digests/multipart/listing/conditional-write behavior, restore and destination cutover without changing manifest bytes or IDs. Mocked S3 fixtures or one successful Put/Get do not qualify a backend. Record unsupported features and source/version bounds; keep source data through validation/rollback.
