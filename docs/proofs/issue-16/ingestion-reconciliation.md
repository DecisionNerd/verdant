# Accepted ingestion reconciliation

On 2026-10-30, accepted main `0c2960123437485cdf5f93cceafc5cf613ceb6c6`
(#53, #14) was merged into the owned quality branch without rewriting its history.
The accepted ingestion, source, upload, preview, cleanup and execution paths are
preserved alongside the existing quality REST/MCP/UI paths.

The accepted main already contains the candidate implementation inherited by
this branch. A read-only comparison against `49c0470` found 12 candidate source,
test, migration and proof paths byte-identical; candidate REST/MCP function bodies
and original contract/model classes also matched. This does not close #15: its
owner's independent Chrome-session proof remains an explicit gate in #53 and the
candidate receipt. That worktree, runtime and browser fixture were not changed.

The canonical migration chain now runs through `0018_ingestion_sources`, followed
by the quality migration resequenced to `0019_quality`. No accepted migration was
edited. The earlier unmerged `0016_quality` isolated proof databases remain
retained; they were not reset, stamped or treated as a canonical upgrade source.

Verification of this integrated quality snapshot:

- **301 backend tests passed, no skips**, in 82.59 seconds, using PostgreSQL 17.6
  and the existing owned SeaweedFS 4.51 fixture. Fresh database
  `verdant_quality_reconciled_20261030_test` upgraded from zero to `0019_quality`.
  The suite includes the accepted ingestion/storage cases and quality policy,
  validation, recovery, authorization and transport cases.
- **56 UI tests passed**; TypeScript and production build passed.
- Ruff lint/format, Alembic model comparison and whitespace checks passed.

Docker assigned new ephemeral host ports when the stopped owned fixtures were
started: PostgreSQL 60541 and S3 60557. An initial run against the historical ports
failed with connection refusal; the successful run above used the inspected live
ports. Synthetic test credentials and isolated database/object prefixes were used.
No other owner's fixture, provider service or production data was changed.

The [original browser qualification](quality.md) remains evidence for the original
quality snapshot. This reconciliation adds integration regression evidence; it
does not claim a fresh browser run, #15 acceptance or default-branch delivery.
PR #50 remains draft until the retained owner gate is resolved.
