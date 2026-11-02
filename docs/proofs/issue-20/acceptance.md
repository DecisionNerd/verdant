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

The expanded PostgreSQL/S3, blocked-lock revocation and512-member memory proof are
running separately at this native-proof checkpoint; their final receipt follows.
