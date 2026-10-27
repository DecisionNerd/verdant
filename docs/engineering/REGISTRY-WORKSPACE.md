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
