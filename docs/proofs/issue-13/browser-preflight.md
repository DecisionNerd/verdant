# Registry browser preflight

Executed 2026-10-27 against the current issue #13 Compose candidate, schema `0006_dataset_metadata`, canonical local Keycloak. Partial acceptance evidence; issue #13 is not complete.

- Human principal `52ab5089-0255-4f34-add4-ad604a10f8c2` was explicitly granted write on synthetic namespace `00000000-0000-0000-0000-000000000013` through the trusted operator CLI.
- Real browser signed in, selected the workspace using keyboard Down/Return, discovered the existing dataset, and created `Document reading order` without CLI creation. Dataset ID `039fec3e-656d-44d2-93c8-7f77ff713299`; detail URL and page refresh worked.
- Thirty synthetic registered samples referencing the existing synthetic storage probe were prepared through shared services. They are not production document content or ingestion proof.
- Browser selected exact sample revision `c1c34857-e931-474a-8934-87bdd3274219`, added membership, observed draft revision 1, then changed split to `validation`, observing revision 2.
- Two simultaneous browser editors loaded metadata revision 0. First saved `Document reading order reviewed` as revision 1. Second attempted `Second editor preserved input`, received the explicit changed-record message, and retained the entered name. Loading current revision advanced its expected revision to 1 while preserving input. `stale-metadata-editor.png` shows the stale editor inputs; the alert was below that screenshot's viewport. Accessibility output verified the actual alert.
- Two browser draft editors held revision 1. First saved split `validation` as revision 2. Second attempted split `test`, received the explicit conflict message and retained sample/revision/split input. Accessibility output verified this outcome.
- During evidence capture the short local qualification session expired. The UI removed the authenticated workspace and showed `Your session has expired`; screenshot `session-expiry.png` records this state. It is not a screenshot of the preceding conflict.

Remaining: complete catalog/draft pagination against representative larger fixtures, denied/read-only browser identity, keyboard/focus and narrow viewport proof, sample removal, stale/partial/failure states, exact-version inspection scope, and independent review/CI/merge. No completion claim follows from this preflight.

## Read-only, exact inspection and denied-access retest

Against candidate `61bf393`, the operator changed the same qualification principal's grant to read. Fresh browser sign-in and dataset deep-link refresh showed read workspace context, disabled metadata fields, and no metadata save, membership save/edit/remove controls. Exact revision inspection returned revision 1, its exact UUID, SHA-256, byte size and modality without private keys. Opening draft revision 1 showed split `train`; current revision 2 had shown `validation`. `read-only.png` records the read-only page.

The operator then revoked the grant. Browser refresh of the exact dataset deep link showed no granted workspaces and the permission-denied message. Dataset metadata and membership rows were absent. `denied.png` records that state. The operator explicitly restored write afterward for subsequent qualification. This tests current authorization changes for the same stable identity; a separate no-grant service identity is covered by API tests.
