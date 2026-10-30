# Publication over accepted ingestion

Follow-up: the [automated two-session lifecycle test](../issue-15/automated-browser.md)
passed and replaces the manual browser gate mentioned in this historical receipt.

The 2026-10-30 reconciliation merges the quality integration over accepted main
`0c296012` (#53) into the owned publication branch, preserving accepted source,
retry, preview, execution and staging-cleanup behavior. Quality is `0019_quality`;
publication follows as `0020_publication`. Earlier unmerged development migration
databases are retained separately and are not stamped or upgraded through a
rewritten revision.

The API and MCP reuse the accepted injected S3 instance and lifecycle. The required
storage helper delegates to `S3Storage.from_env()` rather than defining another
region/endpoint/credential profile. Conditional immutable manifest writes retain
independent digest verification and exact readback. Accepted source-prefix,
addressing, signing-error and close behavior remain in the shared adapter.

Verification against owned PostgreSQL 17.6 and SeaweedFS 4.51 fixtures:

- **118 focused tests passed**, covering publication, manifest encoding,
  ingestion/candidate compatibility, authorization/transport, linked cleanup and
  storage contracts. The fresh database was
  `verdant_publication_reconciled_20261030_test`, upgraded through `0020_publication`.
- **68 UI tests passed**; TypeScript and production build passed.
- Ruff checks/format, Alembic comparison and whitespace checks passed.
- The new actual-upload integration fixture uses `ready_ingestion`, multipart
  transfer and `IngestionRunner` acceptance, then membership, capture/seal,
  quality validation, publication preparation and finalization. It seeds no
  artifact binding. Linked cleanup removes staging while exact source and
  manifest bytes remain valid; collector claims for both are denied. Replaying
  ingestion and cleanup preserves the accepted result.
- An in-place `0019_quality → 0020_publication` upgrade preserved selected
  existing source, artifact, ingestion and policy rows/counts/content hashes in
  the populated quality test database. See
  [bounded upgrade receipt](accepted-main-upgrade.json). The compared columns
  predate publication; this is not a whole-database backup/restore guarantee.

The original [publication browser receipt](publication.md) remains scoped to its
original snapshot. These checks add accepted-ingestion compatibility and migration
evidence; they do not establish fresh browser acceptance, native publication
dispatch, production capacity or default-branch delivery. #14 is accepted and
closed; #15 remains with its owner for its independent Chrome-session gate.
