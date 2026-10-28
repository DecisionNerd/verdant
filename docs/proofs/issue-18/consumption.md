# Version consumption and availability qualification

Status: #18 is implemented and locally qualified on the owned development stack.
This receipt does not claim default-branch delivery, production capacity, or
closure of #18 or epic #3. Canonical #14/#15 acceptance and landing remain with
their existing owners.

Source: `feature/version-consumption-lifecycle`, based on publication
`7bb5c24a956efa63e512fd185d7e988a4c367dbd` (draft PR #51), above quality
`ab6e09d6ede15d4af40e56188108dac310e618e9` (draft PR #50). The inherited
ingestion/candidate snapshots are `ace40e8`/`49c0470`; they are immutable
development inputs, not acceptance of another owner's work. The source and
receipts are committed together. No other owner's worktree or runtime was changed.

## Scenario evidence

| Completion scenario | Evidence |
| --- | --- |
| Authorized exact version consumption and availability controls | Real Keycloak browser login; exact identities, 25+1 member paging, graph/table lineage, keyboard preview, real manifest/text/binary downloads with independent hash readback, version and dataset archive/unarchive. PostgreSQL/S3 tests exercise native and REST/MCP paths. |
| Failure, stale input, recovery and current authorization | Real read-only login and grant revocation returning 403; cached exact data hidden after refresh. Real PostgreSQL/S3 tests cover missing/corrupt bytes, qualified relocation, authorization during I/O, CAS races, archive/admission lock races and retained checkpoints. |

## Automated evidence

The implementation session completed these checks before browser qualification:

- **311 backend tests passed, no skips**, using real PostgreSQL 17.6 and
  SeaweedFS 4.51. Fresh `verdant_consumption18_final_test` applied the migration
  chain from zero through `0018_availability`.
- The focused cross-layer suite passed **75 tests**. Consumption cases include
  exact manifest streaming; pinned interpretation after later sample/draft edits;
  default and explicit archived discovery; CAS/audit/SQL guards; safe admitted
  checkpoint completion; paused publication; archive winning admission locks;
  permission checks before counts, members and storage and after I/O; scoped
  sample resolution; explicit missing/corrupt errors; verified alternative
  placement; UTF-8 limits; and credential-free descriptor/redirect boundaries.
- **68 workspace tests passed**, including **13 version-consumption cases**.
  Typecheck and production build passed. The full UI suite was rerun after the
  page-scoped lineage warning wording changed, with all 68 passing.
- Ruff lint/format, Alembic model consistency, Compose configuration and
  whitespace checks passed.
- Documentation build passed (10 static pages); all 44 local links in the changed
  README, guides and qualification receipts resolved. The first docs build lacked
  the existing `sharp` dependency; installing the locked docs dependencies fixed
  the setup without a lockfile or dependency change.
- Independent cross-layer source review found no merge-blocking findings.
  CodeRabbit CLI was attempted on the #18 uncommitted diff and failed before
  analysis: organization access denied, HTTP 403. No CodeRabbit clean-review
  claim follows from that attempt or a draft PR's skipped review check.

Reproduce with an isolated PostgreSQL database ending `_test`, and the explicit
disposable S3 fixture variables described in
[object-store qualification](../../engineering/OBJECT-STORE-QUALIFICATION.md):

```sh
uv run --group workflows pytest -q
uv run --group workflows pytest -q tests/test_consumption.py tests/test_auth_routes.py
uv run ruff check .
uv run ruff format --check .
uv run alembic check
pnpm --filter @verdant/workspace test
pnpm --filter @verdant/workspace typecheck
pnpm --filter @verdant/workspace build
git diff --check
```

The backend fixtures use actual S3 writes and independent readback, with synthetic
accepted source bindings. They do not qualify #14's source-ingestion transports.
Authorization overrides in protocol tests are distinct from the real SSO proof.

## Real browser and bytes

Run on 2026-10-28 UTC using the T3 collaborative browser, isolated Keycloak 26.8.1
realm `verdant-consumption18`, Vite port 18805 and API port 18807. The owned
PostgreSQL container exposed port 51600; browser database
`verdant_consumption18_browser_test` was separate from automated test databases.
The owned SeaweedFS S3 fixture used port 51851. The API disabled access logs;
no tokens, signed download URLs or credentials are retained in these receipts.
Synthetic principals and fixture identifiers are in
[fixture-receipt.json](fixture-receipt.json).

Two versions of 26 members were published through the real shared services. One
sample's later metadata correction changed its revision, note and split while
retaining the same source artifact. Actual browser manifest downloads confirmed:

| Pin | release-1 | release-2 |
| --- | --- | --- |
| Captured draft revision | 26 | 27 |
| Sample revision | `74fea756-451b-4b28-95f9-d7146ebfc10e` | `e288e563-908e-4a4d-9d77-e657b2a7f3bc` |
| Metadata note | `original v1` | `correction v2` |
| Split | train | test |
| Manifest SHA-256 | `4bf50cd25447d24bada69aa00682e2dc270488af7b31bbf67093a2130b887e91` | `9d5ac14c9af58b2688c321d647f42990a090db15bde2fc480684dfc6d796232d` |
| Manifest bytes | 14293 | 14294 |

Observed browser actions and results:

1. Owner signed in through actual Keycloak authorization-code/PKCE login.
   Exact v1 and v2 routes showed their respective pinned sample revision/split,
   manifest digest and report/policy IDs. Member navigation produced pages of
   25 and 1, preserving the exact version.
2. A focused preview button activated with Enter. The text preview verified the
   entire 8036-byte source and returned exactly 4096 preview bytes, including
   UTF-8 `café`. The binary sample explicitly reported unsupported preview and
   offered its verified download.
3. The graph and accessible table showed pinned sample/report/policy identities
   and explicitly absent annotation/schema/review/transformation evidence.
   No missing pin was presented as approval. The page-level warning makes no
   assertion about unseen member pages.
4. Actual download anchors saved both manifests, text and the 68-byte PNG.
   Independent local SHA-256 matched each recorded digest. See
   [download-receipt.json](download-receipt.json); it deliberately omits URLs.
   DOM inspection found zero signed storage links. Anchors used application
   download routes.
5. Owner deprecated v1, then archived it. The default version list excluded v1,
   while its selected exact route and manifest download remained available.
   Showing archived versions exposed it. Unarchive restored **deprecated**
   availability rather than activating it.
6. Owner typed an unsaved catalog name, then archived the dataset. The input
   remained intact and disabled, with the original editing revision preserved.
   New capture was disabled and checkpoint/admission effects were explained.
   The exact v1 manifest remained byte-identical. Default dataset discovery
   returned no matching dataset; the archived filter exposed it. Unarchive
   restored admission and discovery. No data deletion was attempted.
7. A separate incognito profile signed in through Keycloak as the synthetic
   namespace reader. Exact v1 preview worked; archive/deprecate/save/capture/
   publish mutation controls were absent. The trusted identity service revoked
   only that synthetic reader's grant. An immediate uncached browser preview
   request returned **403**, `Namespace access denied`. On the next dataset
   refresh the UI removed cached version/member/preview content and displayed
   the permission error. Already delivered bytes cannot be revoked.

Screenshots: [text preview](browser/exact-text-preview.png),
[partial lineage](browser/partial-lineage.png),
[archived version](browser/version-archived.png),
[preserved unsaved input](browser/dataset-archive-preserves-input.png),
[reader exact view](browser/reader-exact-version.png),
[revoked reader](browser/reader-revoked.png).

## Limits and remaining delivery gates

The artifact limit is 256 MiB. Preview retains the first 4 KiB only for supported
UTF-8 text MIME types; binary content has a verified download. Resolution tries
at most four qualified placements in the configured store/bucket. Descriptors
and lineage show recorded identity, not a live byte-availability promise.
Preview/download verifies bytes and rechecks permission before releasing output.
S3 redirects remain valid for up to **120 seconds**, including after a later
grant revocation. Native manifest output uses spooled scratch, spilling after
1 MiB, before its final permission check. These are deliberate bounded behaviors,
not throughput or production-scale qualification.

Safe candidate, validation and publication preparation checkpoints may resume
while a dataset is archived; new dataset-bound work and publication are paused.
Namespace-level sample/ingestion work is not dataset-bound and remains available.
Archive changes availability only. Separate retention/deletion policy is outside
this slice. Migration rollback must preserve published content and backups; see
the [implementation/runbook](../../engineering/VERSION-CONSUMPTION.md).

The #16–18 PR stack still requires canonical #14/#15 acceptance, migration-chain
reconciliation with the accepted upstream implementation, green checks and
landing on main. This proof does not take ownership of those dependencies or
close their issues. No production or paid resource was used.
