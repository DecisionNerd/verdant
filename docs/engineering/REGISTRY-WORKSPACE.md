# Dataset registry workspace

The implemented React/Vite workspace supports authorized catalog discovery, metadata editing, and revisioned draft membership. It is served by the existing backend-owned Keycloak session. It does not publish immutable dataset versions or ingest arbitrary files; those workflows belong to later slices.

## Start and sign in

Follow [local Compose setup](../../infrastructure/compose/README.md). Open http://127.0.0.1:18800 and sign in with an organizational account admitted to the workspace. An operator binds the exact issuer/subject and grants namespace read or write access through the existing [identity operations](../infra/identity-operations.md). Email is display information, never an identity key.

Choose a workspace. Its name and access level provide namespace context; dataset rows show owner identity, modality and metadata revision. Search matches name or slug literally, including wildcard characters. Modality and owned-by-me filters run on the server before pagination and counts. Pages contain at most 25 datasets; the API supports bounded pages up to 100. More than 100 workspaces can be loaded incrementally. Direct authorized workspace lookup supports deep links outside the first workspace page.

## Create and edit

Write access exposes dataset creation. Names, slugs and modalities are validated by shared contracts. IDs and ownership remain stable when metadata changes. Open a dataset to edit metadata or inspect its draft. URLs retain namespace and dataset IDs; refreshing an authenticated deep link restores the same detail view.

Metadata saves carry an expected revision. Concurrent writes produce a conflict instead of silently overwriting another editor. The form preserves input. Load the current revision, compare the latest saved values shown above the form, and review your preserved values before saving again. Read-only access disables mutation controls. Backend authorization is checked independently of controls.

## Drafts and exact revisions

Select an existing registered sample to add its exact revision and assign a split. Edit split or remove membership through the same draft revision boundary. Sample discovery exposes IDs, digests, sizes and modality, not storage keys or private source metadata. Exact sample inspection resolves a selected immutable revision rather than silently following latest.

Draft pages pin their revision while navigating. Open a numbered historical draft revision to inspect its membership; load the current draft before editing old state. Draft saves retain the revision captured when editing began even if background data changes. Conflicts preserve the selected sample, exact revision and split. Explicitly loading current draft rebases that input for review.

## Failure and session behavior

Transient metadata refresh failures retain the mounted editor and display an error. Authorization failures remove protected data. Namespace pagination failures preserve previously loaded workspaces and offer a retry. Initial loading, empty results, denied access and failed requests have separate states.

The session is checked on focus, visibility changes, page restoration and cross-tab session-change signals. Protected views are hidden during verification. Account/session changes replace the principal-scoped query cache. Signals contain no credentials or identity data. Session expiry removes authenticated content; input is not persisted to browser storage.

## Verification

```sh
pnpm --filter @verdant/workspace test
pnpm --filter @verdant/workspace build
VERDANT_TEST_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/ISOLATED_test uv run --locked pytest -q
```

Use a dedicated database ending `_test`. Never point tests at an active registry database. PostgreSQL tests cover current-grant enforcement, literal search, owner/modality filters, 1,001-row keyset pagination, concurrent metadata edits, immutable sample inspection and audit behavior. Component tests distinguish transient transport failure from authorization loss and verify shared-cookie identity changes clear the previous account's cache.

[Browser qualification](../proofs/issue-13/browser-preflight.md) records real creation, edits, conflicts, pages, access changes, exact inspection and logout evidence, with component-only and incomplete checks labeled separately. A successful build alone is not browser acceptance.

## Operations

Choose a workspace, then **Operations** to inspect durable operation status. Links retain the workspace and operation identity, so reloading reconnects to PostgreSQL-backed status. The list and selected detail refresh every three seconds. Connection failures retain the last known status with an explicit stale warning and disable recovery actions. Access denial removes protected details.

Write-authorized users can retry failed attempts, request cancellation for queued/running work, and request trusted reconciliation for nonterminal or failed work. Reconciliation requests contain only the displayed epoch; workers verify provider identity and results. A requested action is not completion. Cancellation remains pending until the logical operation confirms it; a provider run may finish later without changing the cancelled logical outcome.

From an exact sample revision inspection, **Verify artifact** queues verification of that registered revision and links to its operation. Retrying a failed admission request uses the same request identity while the inspection remains mounted. Prior effects and accepted digests are shown without private object keys or provider payloads. Read-only users can inspect operations but cannot invoke recovery controls.

## Frozen candidates

Open a dataset and choose **Capture latest draft** to pin its displayed draft revision. Capture returns a candidate and generation identity; the working draft remains editable. If an admission response is lost, the retry control identifies the original source revision and reuses its request identity.

**Resume preparation** copies at most 100 exact members per request. Each batch persists members and progress together; reopening the candidate URL resumes the same generation. Sealed generation membership cannot change. Candidate selection stays explicit while the latest draft refreshes, and frozen member pages retain original exact sample revisions and splits.

**Selected candidate** selection mode allows a sealed earlier revision. **Current draft** mode instead requires the captured revision to match the latest working draft; a conflict retains selection and preparation. **Check selection** checks that contract only. It neither runs quality policies nor publishes a dataset version. Unannotated members explicitly pin absent evidence. Members can also carry bounded inline annotation/task-schema content, input review records bound to the annotation digest, and transformation code/configuration/environment. None of these values resolves a later current value.


Interpretation evidence can be imported as JSON when saving membership. Blank input preserves existing evidence; `{}` explicitly clears it. Pin changes advance draft revision, even when sample identity and split stay unchanged. Each content envelope contains `format`, `payload`, and an optional `sha256`; the server derives the digest from canonical UTF-8 JSON containing format/payload, and rejects a supplied digest that disagrees. The complete interpretation contract is limited to 64 KiB. Annotation content requires its exact inline task schema. A review input contains `annotation_digest`, `decision` (`accepted`, `rejected`, or `abstained`), and a rationale; it records input data and does not assert a reviewer role or satisfy a quality gate. Transformation format `verdant.transformation.v1` requires actual code text plus configuration/environment objects, rather than an unresolved identifier.

For a changed sample revision with existing evidence, explicitly replace the evidence or clear it; evidence cannot silently move to different sample content. Source-member and frozen-member details display the complete pinned contract. Imported content is stored as data and is never executed or fetched as a URL. Native annotation authoring and quality-policy approval remain owned by their separate workflows.
