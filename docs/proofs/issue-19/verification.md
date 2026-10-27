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
