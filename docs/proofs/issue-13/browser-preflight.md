# Registry browser preflight

Executed 2026-10-27 against the current issue #13 Compose candidate, schema `0006_dataset_metadata`, canonical local Keycloak. Partial acceptance evidence; issue #13 is not complete.

- Human principal `52ab5089-0255-4f34-add4-ad604a10f8c2` was explicitly granted write on synthetic namespace `00000000-0000-0000-0000-000000000013` through the trusted operator CLI.
- Real browser signed in, selected the workspace using keyboard Down/Return, discovered the existing dataset, and created `Document reading order` without CLI creation. Dataset ID `039fec3e-656d-44d2-93c8-7f77ff713299`; detail URL and page refresh worked.
- Thirty synthetic registered samples referencing the existing synthetic storage probe were prepared through shared services. They are not production document content or ingestion proof.
- Browser selected exact sample revision `c1c34857-e931-474a-8934-87bdd3274219`, added membership, observed draft revision 1, then changed split to `validation`, observing revision 2.
- Two simultaneous browser editors loaded metadata revision 0. First saved `Document reading order reviewed` as revision 1. Second attempted `Second editor preserved input`, received the explicit changed-record message, and retained the entered name. Loading current revision advanced its expected revision to 1 while preserving input. `stale-metadata-editor.png` shows the stale editor inputs; the alert was below that screenshot's viewport. Accessibility output verified the actual alert.
- Two browser draft editors held revision 1. First saved split `validation` as revision 2. Second attempted split `test`, received the explicit conflict message and retained sample/revision/split input. Accessibility output verified this outcome.
- During evidence capture the short local qualification session expired. The UI removed the authenticated workspace and showed `Your session has expired`; screenshot `session-expiry.png` records this state. It is not a screenshot of the preceding conflict.

This initial checkpoint had open pagination, access, focus, removal and inspection checks. Later sections record those results. Final review/CI/merge state is recorded with the pull request; this file distinguishes component evidence from browser evidence.

## Read-only, exact inspection and denied-access retest

Against candidate `61bf393`, the operator changed the same qualification principal's grant to read. Fresh browser sign-in and dataset deep-link refresh showed read workspace context, disabled metadata fields, and no metadata save, membership save/edit/remove controls. Exact revision inspection returned revision 1, its exact UUID, SHA-256, byte size and modality without private keys. Opening draft revision 1 showed split `train`; current revision 2 had shown `validation`. `read-only.png` records the read-only page.

The operator then revoked the grant. Browser refresh of the exact dataset deep link showed no granted workspaces and the permission-denied message. Dataset metadata and membership rows were absent. `denied.png` records that state. The operator explicitly restored write afterward for subsequent qualification. This tests current authorization changes for the same stable identity; a separate no-grant service identity is covered by API tests.

## Transient failure regression

A live API-stop attempt overlapped qualification-session expiry. The resulting session-check unavailable page does not prove background-input preservation; API service was restarted and readiness recovered. No browser success claim is made for that attempt.

Controlled React/Query integration tests (`apps/workspace/src/dataset.test.tsx`, jsdom) prove that a transient 502 background refetch retains the mounted editor and unsaved name, while a subsequent 403 removes protected metadata. Both tests and the production build passed. CI runs these tests. This is component-level evidence with mocked transport, separate from real browser evidence.

## Catalog pagination and focus

Sixty synthetic datasets were added through shared services, producing 62 visible datasets in the qualification namespace. Real browser selected the workspace with Down/Return, moved through pages 1, 2 and 3, retained total 62, and disabled Next on final page. `catalog-pagination.png` shows final-page context. Separate PostgreSQL test covers 1,001 datasets across 11 pages with exact sorted identities and no gaps/duplicates.

Route transitions now focus the catalog heading, and loaded dataset detail focuses its heading. The component regression asserts dataset focus while preserving unsaved input on failed refresh. Production build and both component tests passed; the latest focus changes still require a runtime keyboard retest.

## Draft pages, removal and cross-tab logout

Against runtime `d79d280`, 30 synthetic memberships were prepared through shared commands. Browser loaded revision 31, paged from 25 members to the final 5 with Next disabled, then removed a synthetic member. The UI confirmed `Membership removed.`, showed revision 32, and reset pagination to first page. Dataset detail received keyboard focus on its heading after loading.

Two actual browser tabs shared the authenticated session. Signing out in tab 2 caused tab 1 to remove its dataset and authenticated catalog automatically, before the organizational logout confirmation was completed. `cross-tab-logout.png` records tab 1 showing the organizational sign-in entry. Tab 2 then completed Keycloak logout and showed signed-out state. Different-account cache replacement is additionally covered by the controlled workspace component regression.

## Final local gate

All 81 PostgreSQL/backend tests passed. Five controlled React/Query tests passed, including partial namespace-page failure preserving edits, valid deep links outside page 1, 502 preservation, 403 removal, and shared-cookie account replacement. Typecheck/build passed. Independent final review at `579fd17` found no remaining merge blockers; the subsequent change adds regression tests only. Live outage-over-session-expiry remains labeled inconclusive rather than used as acceptance evidence.
