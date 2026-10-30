# Lifecycle reconciliation with accepted main

On 2026-10-30 the owned #50 → #51 → #52 stack was reconciled with accepted main
`0c2960123437485cdf5f93cceafc5cf613ceb6c6` (#53, #14). This retains accepted
ingestion/source/retry/cleanup/preview behavior and adds quality, publication and
consumption above its canonical migration history:

`0018_ingestion_sources → 0019_quality → 0020_publication → 0021_availability`.

Accepted migrations were preserved. Unmerged development revisions were resequenced
only in these owned branches. Their old isolated databases remain retained;
they were not reset or stamped into the canonical chain. No #15 owner worktree,
runtime or retained browser fixture was changed.

## Combined verification

- **394 backend tests passed, no skips**, in 117.26 seconds using real
  PostgreSQL 17.6 and SeaweedFS 4.51. Fresh database
  `verdant_consumption_reconciled_20261030_test` applied the canonical chain from
  zero through `0021_availability`.
- **92 UI tests passed**, with TypeScript and production build passing.
- Ruff lint/format, Alembic model consistency and whitespace checks passed.
- The new integration regression begins with an actual multipart upload and
  accepted ingestion result, without SQL-seeded artifact bindings. It carries
  the exact revision/artifact through capture, validation, publication, linked
  staging cleanup, archive and retained manifest/preview reads.
- The accepted missing-object HTTP contract remains **404**; corruption is **409**
  and unavailable storage **503**. The older unmerged consumption assertion of
  missing-object 409 was changed to preserve accepted ingestion compatibility.
- Independent integration review caught that the browser request helper discarded
  safe 404 details, reducing missing bytes to a generic service failure. The helper
  now retains bounded 404 details with an explicit fallback. A test sends an actual
  fetch 404 through that helper into the preview UI and confirms old bytes disappear;
  two fallback cases reject absent or oversized error details. All 92 UI tests
  and the build were rerun after this fix; backend source is unchanged from the
  394-test snapshot.
- API/MCP consumption and publication reuse the accepted shared storage instance
  and its configured source-prefix, signing, region/addressing and close behavior.

The [quality reconciliation](../issue-16/ingestion-reconciliation.md) and
[publication reconciliation](../issue-17/ingestion-reconciliation.md) record their
focused evidence. Counts describe each tested snapshot and must not be added.

## Populated accepted-schema upgrade

The [upgrade receipt](accepted-main-upgrade.json) records a separate in-place
upgrade of `verdant_accepted18_upgrade_20261030_test`:

1. Apply only `0018_ingestion_sources` to a new owned database. Execute source
   from an immutable archive of accepted main `0c296012`, with its installed
   dependencies. Provision a synthetic authorized identity through IdentityService.
2. Actually upload, complete and accept a 62-byte object through the accepted
   IngestionService/IngestionRunner. Add its exact revision to a dataset and
   capture/seal a one-member candidate. Retain the live source and registry data.
3. Upgrade that populated database through `0019_quality`, `0020_publication` and
   `0021_availability` with the reconciled source. No schema reset, raw artifact
   binding insertion or revision stamp is used.
4. Confirm the original candidate/generation/binding and actual source digest.
   Validate, prepare/finalize a version, run the originally admitted linked
   staging cleanup, archive dataset/version and consume the exact retained bytes.

The source SHA-256 is
`cd4d301f388bb4eecaf2f2327afe7bd995a8d0dfc6a1b9e0516a534f91ae3de1`.
The produced 1854-byte manifest has SHA-256
`caf44e1d97f249f79ab4ba6ac0bd53b65ee1b9635cf827e9dc530f1ac40cbc3e`.
Manifest bytes are identical before and after archive. Both source and manifest
collector claims remain denied after successful staging cleanup. Exact fixture
identities are in the receipt; credentials and signed URLs are omitted.

## Acceptance boundary

The original [real Keycloak/browser qualification](consumption.md) remains scoped
to its original immutable snapshot. This reconciliation proves backend/native
integration, schema compatibility and UI regression behavior; it does not claim
a fresh browser session or native Prefect publication dispatch.

#14 is accepted and closed. Accepted main contains the same candidate code as
`49c0470`, but PR #53 and the original candidate proof explicitly retain #15's
independent Chrome-session gate. That acceptance and closure remain with its
existing owner. The #50–52 stack therefore stays draft and unmerged, and epic #3
remains open. No production, paid resource or capacity claim is made.
