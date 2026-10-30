# Automated two-session lifecycle acceptance

The remaining #15 browser gate is covered by an executable Playwright regression,
not a manual Chrome or extension-dismissal requirement. The complete test passed
locally in approximately 1.5 minutes with real Chromium, Keycloak, PostgreSQL and
SeaweedFS. This also provides fresh integrated UI evidence for #16–18.

Source: `apps/workspace/e2e/lifecycle.spec.ts`, pinned `@playwright/test` 1.64.1.
The fixture uses the same pinned PostgreSQL 17.6, Keycloak 26.8.1 and SeaweedFS 4.51
images as the local platform. `scripts/seed_lifecycle_browser.py` admits and
accepts 101 actual multipart source uploads in batches of 100 and 1, then creates
authorized membership with non-null typed annotation/schema/review/transformation
pins. It inserts no fake verified artifact bindings and does not bypass browser
authentication. The generated users, credentials, namespace and source text are
synthetic and confined to the disposable test project.

## Assertions executed

1. Two independent Chromium contexts authenticate through actual Keycloak forms
   as different owner/editor identities, with separate cookie jars.
2. The owner captures draft revision 101 and prepares 100 members. The test stops
   and restarts only its own API process. Reloading the exact candidate URL
   retains the candidate, generation, source revision and 100-member checkpoint.
3. The editor changes the last, still-unprepared member's split and every
   interpretation pin through the UI. Latest draft advances to 102. The owner
   resumes the final member and seals the original 101-member generation.
4. Every frozen member is compared with the seed receipt: exact sample revision,
   original split and all original interpretation pins. All five UI member pages
   are navigated as well as current authorized API reads.
5. The owner authors a minimum-101 policy and completes validation in bounded
   batches. The editor changes the draft again to 103. Candidate/generation and
   the exact passing validation report stay bound to source revision 101.
6. Explicit current-draft selection returns HTTP 409 without losing the sealed
   generation. Selected-candidate selection succeeds.
7. The owner prepares/publishes the exact candidate in the UI using keyboard
   confirmation. An actual manifest download hashes to its recorded SHA-256;
   all 101 manifest members retain the original pins/splits. Exact source preview
   bytes hash to their recorded identity, and accessible lineage is present.
8. Dataset archive disables new capture. Archived dataset/version exact manifest
   reads/downloads remain byte-identical. Unarchive restores admission.
9. The trusted identity boundary revokes only the synthetic editor's grant.
   Its open UI removes protected candidate content and shows permission denial.

Exact candidate/generation/report/version IDs and manifest digest are in
[the receipt](automated-browser-receipt.json). A [final screenshot](browser/automated-lifecycle.png)
is retained. Cookies, tokens, signed URLs, authentication traces and HAR files are
not included.

## Repeat and CI

Run `pnpm --filter @verdant/workspace test:e2e` after installing locked Python/
workspace dependencies and Chromium as described in [Testing](../../engineering/TESTING.md#automated-authenticated-lifecycle).
The command performs fixture startup, seeding, API restart, assertions and teardown
without user interaction. Reserved loopback ports are checked before startup.
Unique disposable containers and owned process groups cannot attach to another
owner's worktree/runtime. The normal platform's data and the original #15 owner's
retained browser fixture remain untouched.

The `lifecycle-browser` job now runs the same test automatically for PRs and main.
The existing backend proof (394 tests) and UI regression suite (92 tests), plus
storage/race/upgrade proofs, remain complementary evidence. This test does not
claim production capacity, native Prefect publication dispatch or optional
provider qualification.
