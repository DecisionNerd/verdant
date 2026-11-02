# Process/model acceptance — issues20/21

The automated browser/native run passed on2026-11-02 in1.3minutes:

```sh
pnpm --filter @verdant/workspace exec playwright test --config playwright.process.config.ts
```

Product source: `9a9c968` (after implementation `7fc1c4e` and hardening `5f58bfe`,
`3b4f515`); this proof commits the passing harness's refresh/accessibility-locator/
post-reload selection corrections. [Receipt](native-browser-receipt.json) and
[screenshot](browser/process.png) are retained. Authentication traces/cookies and
Prefect's temporary database are deliberately not committed.

## Actual native/browser evidence

- Real organizational Keycloak browser login; no test authentication bypass.
- Browser-created two-step flow version1 over a sealed three-member candidate.
- Durable queued pause, API process restart, reload, resume into attempt2.
- Actual Prefect3.9.0 server, registered fixed process deployment and process worker.
- Native run `b8d90e2f-c37c-40d1-9b94-76bbc2809d86` reached `COMPLETED`, with exact
  persisted invocation/deployment/parameter matching. Logical operation
  `c902b54d-333e-43ae-977b-1bd50dd6d884` accepted six immutable step outputs, all in
  epoch2; output text and sample/revision/receipt/digest lineage were asserted.
- Actual DataChain0.58.3 executed verified SeaweedFS/S3 text artifacts through the
  shared service, producing expected trim/lowercase results.
- Browser-created Gemini profile revision1 and GLM revision2 with explicit mutable
  alias disclosure, switched profile assignments across flow revisions2/3, and
  asserted pinned profile/configuration digests differ. Responses omit references;
  worker credential status remains truthfully `worker_unverified`.
- A model run with provider calls disabled failed with `provider_calls_disabled`;
  browser recovery retained its logical operation, advanced the epoch, and safely
  cancelled. No external model call was made.

The first preliminary harness attempts completed native work but stopped on test
locator/selection assumptions. The passing run uses the combobox's accessible role,
explicitly reselects the saved flow/input after reload, and exercises the visible
refresh control after disconnection. These were harness corrections, not waived
assertions or a test-only product path.

## Environment and limits

Own Compose project `verdant-process-browser-63267`, ports19132/19133/19181/19185/
19187, database `verdant_process_test`; native Prefect API19100. Only that project's
containers and child processes were started/stopped. Annotation192xx and root193xx
were left untouched. Images are digest-pinned in
`apps/workspace/e2e/process-compose.yaml`; the dedicated realm is in
`process-realm.json`. Local Python3.12, locked Prefect/DataChain dependencies, actual
PostgreSQL17.6 and SeaweedFS were used. Browser stack was fully released after proof.

This is a bounded text slice:1–1000 members,1–8 fixed sequential steps, at most1000
model calls,64KiB input/output text, pages of25, and worker/provider deadlines.
See [the runbook](../../engineering/PROCESS-FLOWS.md) for exact contracts, migration
ordering, conservative uncertain-effect handling and the bounded live command.

## Fixture versus live gate

Provider request/response/error/usage/timeout/compression fixtures use HTTPX unread
wire streams. They prove protocol contracts, not live provider availability, model
quality, current billing, or an immutable provider snapshot. Actual Gemini/GLM
credentials/budget were not supplied for this child. Paid calls:zero. The runnable
operator-authorized live qualification remains parent-owned and must pass separately
before issue21's live gate can be closed.

## Final shared-service and memory proof

The final PostgreSQL/S3 suite passed **84 tests in111.78seconds**:

```sh
scripts/test_process.sh tests/test_model_profiles.py tests/test_process_flow.py \
  tests/test_process_memory.py tests/test_operations.py tests/test_dispatch.py \
  tests/test_registry.py -q --tb=short --basetemp=/tmp/verdant-process-final-proof
```

It includes the namespace-lock/revocation regression, profile contracts and versioning,
strict held-out metadata exclusion, exact step/member/receipt acceptance, confirmed
rejection recovery, unknown accepted-call timeout without replay, pending pause/cancel
intent on success and failure, pure-transform interruption recovery, stale epochs,
unauthorized callers, immutable persistence, and existing operation/dispatch/registry
behavior. HTTP provider fixtures are explicitly simulated; PostgreSQL/S3/DataChain are
actual implementations.

The [memory receipt](bounded-memory.json) records512 independent frozen sample
identities reading the same verified65,001-byte immutable S3 object:33,280,512 source
bytes verified,21 DataChain pages, at most25 members per page. Incremental traced
Python allocation peak was3,929,088 bytes; measured process maximum RSS was296,468,480
bytes, including native dependencies. The test asserts a24MiB Python allocation cap
and page cardinality. It does not claim an unmeasured whole-host or arbitrary-corpus
memory limit.

`alembic check` passed with **No new upgrade operations detected**. Final Python
Ruff/format checks, E2E TypeScript and the workspace build passed; the workspace unit
suite passed96 tests. Python3.12.11, macOS26.6 arm64, Prefect3.9.0, DataChain0.58.3,
HTTPX0.28.1 and SQLAlchemy2.0.55 were used. See [verification](verification.json) for
commands, exact changed files and remaining gates.

The final shared-service/memory fixture used only `verdant-process-proof`, database
`verdant_process_test`, ports19132/19133, with no auth or Prefect service running.
Its two containers/network were removed after acceptance. No running child process
fixtures remain. Root193xx fixtures were never removed or reset.

## Parent integration delta

After the native child proof, the parent found and fixed default MCP serialization of
`FlowDefinition`: `ConfigDict(serialize_by_alias=True)` preserves the REST/store
`schema` alias during FastMCP model serialization and digest reproduction. The parent
owns its real FastMCP-to-typed-SDK parity regression and the merged migration head.
This child did not overwrite that parent-owned change; the native proof above covers
REST/direct-domain execution on the stated child source. Combined SDK/MCP/auth and
annotation acceptance remains the parent's integration proof, rather than an implied
result of this isolated child run.
