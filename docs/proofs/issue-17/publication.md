# Immutable publication qualification

Status: #17 implementation verified on its isolated development stack. This
receipt does not close #17, lifecycle epic #3 or its upstream dependencies, and
does not claim production/default-branch delivery.

Source: owned branch `feature/immutable-dataset-publication`, developer base
`41935d4b03b2b436777e72cf5265bdada96f2b16`, plus this publication implementation.
Its immutable dependency snapshots are ingestion `ace40e8`, candidate `49c0470`
and quality `ab6e09d` (draft PR #50). The existing #14/#15 sessions retain their
worktrees, runtimes, canonical acceptance and landing. This work used a separate
checkout, databases and object/auth fixtures; no other owner's runtime or data
was reset. The development migration chain is
`0014_interpretation → 0015_ingestion → 0016_quality → 0017_publication`.

## Automated evidence

The parent implementation session recorded these completed checks against its
owned fixtures:

- Full backend: **292 passed, no skips**, using real PostgreSQL 17.6 and the pinned
  SeaweedFS target. Fresh database `verdant_publication17_final_test` on the owned
  PostgreSQL port 51600 applied migrations from zero through `0017_publication`.
- Publication suite: **31 passed**, including the two additional capacity/scratch
  failure cases; the earlier integration snapshot had 29 passing cases.
- Pure manifest suite: **41 passed**. The fixed literal vector is **844 UTF-8
  bytes**, SHA-256
  `f48286dc1085aeb0fcaaa7d472acb208e891cf2e5110e6c160f994b8ec6c95fa`.
  It includes literal Unicode `café` and no trailing newline.
- Workspace: **47 passed**, including **12 publication component tests**;
  typecheck and production build passed.
- Latest focused authorization/transport run: **18 passed**, including MCP
  publication admission/get, premature finalization/error behavior and REST
  rejection of a client-supplied readiness body. This is protocol-fixture evidence,
  separate from the real SSO browser exercise below.
- Full Ruff lint/format, Alembic model consistency and whitespace checks passed.

The relevant entry points are `tests/test_manifest.py`,
`tests/test_publication.py`, `tests/test_auth_routes.py` and
`apps/workspace/src/publications.test.tsx`. To repeat the checks, configure an
owned PostgreSQL database whose name ends `_test`, plus an explicit disposable
S3 qualification target, following [object-store qualification](../../engineering/OBJECT-STORE-QUALIFICATION.md).
The test fixture uses `VERDANT_TEST_DATABASE_URL` and
`VERDANT_STORAGE_TEST_ENDPOINT`, `VERDANT_STORAGE_TEST_BUCKET`, optional
`VERDANT_STORAGE_TEST_BROWSER_ENDPOINT` and local-only
`VERDANT_STORAGE_TEST_LOCAL_HTTP=1`. Supply the fixture's authorized storage
credentials through the existing test environment; do not point at production.

```sh
uv run --group workflows pytest -q
uv run --group workflows pytest -q tests/test_manifest.py tests/test_publication.py
uv run --group workflows pytest -q tests/test_auth_routes.py
uv run ruff check .
uv run ruff format --check .
uv run alembic check
pnpm --filter @verdant/workspace test
pnpm --filter @verdant/workspace typecheck
pnpm --filter @verdant/workspace build
git diff --check
```

The database fixture runs Alembic upgrades and the storage fixture creates and
cleans its own random object prefix. Its source artifacts are actual immutable
writes with independent byte readback; accepted registry bindings model the
trusted ingestion boundary. These tests do not qualify #14 ingestion transports.

## Integrity, recovery and concurrency coverage

Manifest tests verify byte-repeatability across dictionary insertion order,
logical identity without storage placement, nested contract validation, exact
member count, strictly increasing unique UUIDs, bounded writes, short-write
completion and failure without a success digest. Capacity is tested by lowering
the output budget rather than allocating an oversized manifest. The runner tests
capacity and unavailable scratch as typed failures, retaining the checkpoint and
protection without claiming readiness.

Real PostgreSQL/object tests cover exact passing report/policy/generation binding;
missing binding, missing/corrupt source and failed independent manifest readback;
checkpoint replay and process-independent resume; lost manifest-write response;
epoch fencing after another claim, renewal or release; retained current-draft
conflicts; published immutability and protection; explicit and managed bounded
release; and lost delete-response reconciliation. Registered source bindings stay
protected after an abandoned group is released: no source-deletion policy is
implied by the collection tests.

Competing finalizers return one version and one success event. Injected audit
failure rolls back version, group, operation, audit/event and outbox together.
Tests revoke grants during source I/O and while preparation/finalization waits on
locks. Expiry during a finalization lock wait uses a fresh database clock.
Collection/reference races recheck the artifact guard after waiting.

The query test records **application-issued SQL** for finalization at 1 and 100
members and asserts identical statements, no generation/reference/location scans,
and no remote storage call. Static inspection of the PostgreSQL publication
trigger places membership/reference scans in `building → prepared`, with fixed
aggregate checks in `prepared → published`. This supports the finalization
ownership boundary. A separate [bounded measurement](finalization-measurement.json)
recorded 24 application statements and 26 base-table relation/mode lock entries
across 15 tables for both sizes. Observed finalization times were 25.495 ms and
27.436 ms, including observation overhead, with one sample per size. Index lock
entries differed (49 versus 46); the cause is unresolved. This does not measure
trigger-internal statement count, contended lock latency, maximum dataset size or
production throughput.

Component tests cover exact passing-report selection, pagination, candidate-switch
response races, bounded checkpoints, immutable manifest inspection, expired renewal,
explicit release, source failure, retained revision conflicts, input/request-key
preservation after unknown responses, identical finalization retry, recovery of an
already committed version, read-only inspection and hiding cached evidence after
authorization denial. A successful component mutation is not object-store proof.

## Real browser and object fixture

The browser exercise used actual authorization-code/PKCE SSO with two distinct
users, not an authentication bypass:

- Keycloak 26.8.1 image
  `sha256:a78d6b79572168cb25886b80a8b5b8d4ed4390743e3eecb810538683e209dd6b`;
  realm `verdant-publication17`, loopback issuer port 18796.
- Workspace port 18795 and API port 18797; isolated PostgreSQL on port 51600.
- SeaweedFS 4.51 (`ffa87c25f`) image
  `sha256:f7a49e3f3972ebed5be2ed4befa4a0c68eb7bdddba6e692d42729ceb545134b8`,
  owned object endpoint port 51851.
- 101 synthetic candidate members sharing one real independently verified source
  artifact. This proves real object verification/retention with repeated logical
  membership, not 101 distinct source uploads or ingest transport acceptance.

Fixture identities:

| Record | Identity |
| --- | --- |
| Namespace | `8aa76383-c3cf-40cf-99f4-7664971e8d32` |
| Dataset | `70b8b68d-0b73-4d27-b48d-2786a38fbf6c` |
| Candidate | `c8c30624-fccd-4153-82a3-8e2cdb91ea25` |
| Generation | `cc0633ff-5a68-4518-9f9d-afee4a4b442e` |
| Passing report | `a04b11fe-a45c-4c2e-ad63-d3ba41a7cbd1` |
| Policy | `81462403-f481-45b3-bba0-964c12f0067f`, revision 1 |
| Published version | `049ec387-8979-4ad5-8e52-3fc4ff9172f0`, label `release-1` |

Observed flow:

1. Owner logged in through real Keycloak SSO and started preparation against the
   exact source-101 candidate and passing report. Two 25-member source-check
   commands saved **50/101**. Only the owned API process was restarted; browser
   reload reopened the durable preparation, then resumed checks to **101/101**.
2. The trusted runner constructed, conditionally stored and independently verified
   the **49,183-byte** manifest. Its SHA-256 was
   `92933c1f58c548b3c7dc45436dd3ebf3a2f3db86cdfa4c9252eada998896fac6`.
   The workspace showed `prepared`, the exact gate and the retained manifest.
3. A distinct editor logged in through real SSO in incognito and changed the
   working draft from **101 to 102** while the owner retained the prepared
   candidate. The captured generation/report did not change.
4. The owner focused the publication heading, used Tab to focus **Publish immutable
   version**, and pressed Enter. Selected-candidate mode published version
   `049ec387-8979-4ad5-8e52-3fc4ff9172f0` as `release-1`, retaining source 101
   while the latest draft was 102.
5. A second preparation, label `current-draft-check`, pinned the same candidate,
   report and **identical manifest digest** with `current_draft` mode. Finalization
   returned HTTP 409 with the useful source-101/latest-102 conflict and retained
   `prepared` state. The final UI explicitly warns about a known draft mismatch.

Screenshots are bounded receipts of the synthetic workspace:

- [Checkpoint 50/101](browser/checkpoint-50.png).
- [Prepared exact manifest](browser/prepared-manifest.png).
- [Distinct editor's draft 102](browser/editor-draft-102.png).
- [Published selected candidate during editing](browser/published-during-edit.png).
- [Current-draft conflict retaining preparation](browser/current-draft-conflict.png).

The conflict screenshot includes the final mismatch warning and server conflict;
the verified preparation remains intact. The [live readback receipt](manifest-receipt.json)
records the same exact digest, original 101 train splits, and two unique protected
references (one shared source artifact and the manifest). No signed URLs,
credentials or raw private payloads are included in these receipts.

## Remaining gates

Pre-PR CodeRabbit CLI authenticated but its remote review could not start because
the service returned an organization-access HTTP 403. No CodeRabbit review is
claimed; no organization, login or billing setting was changed. Independent
read-only reviews covered the service/runner, persistence guards and UI separately.

This is local implementation, integration and browser qualification on an owned
dependency stack. The publication runner is trusted synchronous Python using the
existing registry artifact account; no new service, external account or paid
resource was introduced. Native Prefect publication dispatch is not proved here.

Canonical #14 ingestion and #15 candidate acceptance/landing remain with their
existing owners. Reconcile the development stack with accepted dependency/main
history before landing, then rerun affected checks and CI. The dataset availability
column in this #17 snapshot supports publication guards. The later #18 stack adds
[exact-version consumption and availability controls](../../engineering/VERSION-CONSUMPTION.md)
with [its own qualification](../issue-18/consumption.md); those changes and their
acceptance are outside this historical `0017_publication` receipt.
Production/cloud qualification, capacity certification, issue
closure and default-branch integration are not claimed.

Implementation, operation/retry contracts and additive rollback guidance are in
[Immutable dataset publication](../../engineering/IMMUTABLE-PUBLICATION.md).
