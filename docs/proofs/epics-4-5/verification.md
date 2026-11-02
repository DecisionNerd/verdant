# Integrated epics 4 and 5 proof

Accepted base: `cf2b74cf01f46a226b7d32cd6b3d8a60e87c2ab3`.
Application source for the final runs equals PR54 head
`a3598b6ed11f9db6a7aced465864af7f19e86dce`; subsequent changes refine the
browser harness and documentation. Independent reviews cleared the domain and
transport fixes. No original owner's checkout/runtime was changed.

## Database, object bytes and transports

The combined PostgreSQL/S3 suite passed **527 tests with no skips** in349.32s,
including all24 annotation SQL cases and the real S3 model-input boundary.
Two newly added in-flight pause/cancel parameterized cases passed separately in3.49s;
these counts describe their runs and are not an additive coverage claim.
Alembic reported `No new upgrade operations detected` at `0023_flows_truth`.
The merge joins additive `0022_process` and `0022_annotation` over accepted schema21.
Accepted migration files are unchanged. The final native machine-origin uniqueness
constraint is present and verified in the fresh combined truth deployment.

The owned populated schema21 fixture upgraded in place. Two preexisting immutable
versions retained digest-verified manifest bytes/identities; see
[the retained-version receipt](populated-upgrade.json).

SDK tests execute actual multipart ingestion, candidate capture/quality validation,
publication, manifest/source verification, pagination and REST/MCP reads with both
human and service identities, then deny revoked access. Signed JWT/JWKS delivery
is an explicit identity fixture there. Real Keycloak browser tests provide separate
identity evidence. Fragment deadlines, compression-before-admission, command retry
eligibility, credential/cookie isolation and canonical alias/hash parity have focused
regressions. The generated client/OpenAPI drift check passes.

## Actual browser and worker execution

The [native process receipt](../issue-20/native-browser-receipt.json) records real
Keycloak SSO, flow/profile versioning, API restart, pause/resume into epoch2, native
Prefect completion, six accepted DataChain step outputs over three frozen members,
profile switching and disabled-provider recovery/cancellation. It made zero paid
model calls. The [memory receipt](../issue-20/bounded-memory.json) records512 members,
33,280,512 verified source bytes,21 pages/max25 members per page,3,929,088 traced Python
peak bytes and296,468,480 RSS bytes using actual DataChain0.58.3. These are bounded
local measurements, not a production capacity promise.

The final truth harness passed twice consecutively with fresh datasets/native
projects inside one retained admitted namespace/cohort. Both runs use real HTTPS
Keycloak, the pinned external OSS stock editor, two human identities, five tasks,
ten native annotations/export, verified imports, agreement80%, canonical review and
immutable correction, validation derivation and reviewed production audit probability
0.25. Unchanged holdouts remain identical/nonexploratory and old truth/membership
versions remain readable. No manual browser handoff is required.

- [Run 1](truth-run-1.json)
- [Run 2](truth-run-2.json)
- [Repeatability, schema and retained-row verification](truth-repeatability.json)

The harness starts the path-owned deployment even when stopped, upgrades schema,
creates new per-run data, checks host ports and child liveness, waits for asynchronous
native annotation tabs, and distinguishes new-submission POST from existing-record
PATCH before accepting the correct human author. Private bounded failure receipts
remain ignored; retained original fixtures/data were not reset. Native Community
visibility is explicitly shared trust, never project-name isolation or blindness.

## Product bounds and remaining external requirement

Supported execution is fixed sequential text steps,64KiB verified UTF-8 input,
finite pages/call/time budgets and recorded immutable outputs. Supported annotation
is single categorical text with bounded assignments/history/truth members.
Experiment-engine authority is explicitly unavailable in capability discovery.
Provider protocol fixtures are not live vendor qualification. Issue21 explicitly
requires authorized Gemini/GLM credentials and a request budget; neither was supplied
for this delivery, so paid calls remain disabled and that live criterion stays open.
No production/cloud capacity, native publication dispatch or optional Evidently/MLflow
qualification is claimed. Current operator pages document these exact boundaries.
