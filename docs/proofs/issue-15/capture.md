# Frozen candidate capture progress

The automated [two-session lifecycle regression](automated-browser.md) now supplies
passing completion evidence for the former manual browser gate. The notes below
retain the original implementation history; their pending statements are historical.

This task branch integrates accepted main `be4a945` through merge `e7d9921`. Candidate migrations are `0012_candidates` and `0013_generations`, after the accepted operations migration chain. No #15 completion is claimed; schema-14 runtime qualification is recorded below.

The first implementation captures candidate/source draft revision/generation identity under the existing draft lock and current write authorization, with an idempotent request key/digest and atomic audit. A repeated request resolves its original capture before checking the latest draft revision, so later edits cannot silently recapture different data. Source and latest revisions are returned separately. Database triggers reject capture-identity mutation and deletion.

Four tests ran against isolated real PostgreSQL database `verdant_issue15_test`: retained capture after a membership edit, conflicting/stale input rejection, competing capture producing one audit/identity, denied read-only capture, audit rollback, and direct persistence mutation/deletion rejection. Ruff passed. These tests cover capture only. Bounded preparation, immutable generation membership and interpretation pins, sealing, candidate UI/MCP/REST, current-draft conflict behavior, browser proof, and delivery remain unfinished.


Bounded preparation now reads each keyset page from the retained captured draft revision. Candidate locking serializes competing preparers; expected-count commands make repeated batch delivery reuse committed progress. Generation rows and cursor/count/state/audit commit together. The final batch seals the candidate. Database insertion guards lock the same candidate row before admitting a member, while member update/delete and post-seal insertion/progress mutation are rejected.

Eight real-PostgreSQL tests passed, including edits between preparation batches, duplicate batch/resume, competing preparers, exact original sample/split membership, rollback on preparation-audit failure, and direct persistence immutability. Interpretation schema `verdant.membership.v1` currently pins explicit absence of annotation/task-schema/review/transformation evidence because those owning domains do not yet exist. Absence never means latest. Future integration must supply real immutable namespace-scoped evidence; no arbitrary provider IDs are accepted here. UI, transport, current-draft conflict, broader interpretation integration, and complete #15 qualification remain unfinished.


After integrating accepted #19 main, all 128 backend tests passed against a new isolated `verdant_issue15_integrated_test` database. The earlier capture-only test database was retained, without resets. Ruff/format across 65 Python files passed; model/migration consistency is checked separately.

Candidate list/detail and generation membership reads now enforce current namespace access before disclosing rows/counts and scope identities to their dataset. Membership pages take a bounded shared candidate lock so prepared rows and count/state remain consistent while another preparer runs. Explicit selection checks require sealed preparation. Selected-candidate mode remains usable after draft edits; current-draft mode compares captured/current revisions under the draft lock and returns a useful conflict without altering preparation. Actual publication finalization remains #17's responsibility and must reuse the guard inside its own short transaction.

REST and MCP expose the same capture/prepare/read/selection services with bounded pages. Ten real-PostgreSQL versioning tests pass, including current-read revocation, generation pagination, and a current-draft conflict preserving sealed membership/identity. Existing auth-route composition tests also passed. Candidate UI and real browser proof remain unfinished.

The dataset workspace now includes candidate capture, stable deep-linked selection, bounded preparation resume, frozen member pages, and explicit selected-candidate/current-draft checks. Latest draft refresh does not replace selection. Component tests cover draft advance plus retained selection/conflict, transient outage preserving frozen content, revoked access hiding it, read-only controls, and response-loss admission retry using the original request/source revision. Twenty workspace component tests and typecheck passed; production build passed. These are component/build checks; authenticated two-session browser and runtime migration qualification remain required.


Independent review correctly identified the initial null-only interpretation implementation as insufficient for AC2. Migration `0014_interpretation` adds typed interpretation content to historical draft membership and extends its interval-closure guard. Annotation/task-schema envelopes carry actual bounded content with verified derived digests; review input binds the exact annotation digest; transformation content requires code/configuration/environment. Pin changes advance the draft; preparation copies the source interval's contract verbatim. Ordinary split edits preserve evidence; changing sample revision with nonempty evidence requires explicit replacement or clearing. UI imports/display bounded evidence JSON without introducing an annotation editor or claiming quality approval.

The full backend suite passed 132 tests on a fresh isolated `verdant_issue15_evidence_test` database. A non-null regression changes annotation, schema, review, transformation and split after capture, then proves the generation retains every original pin. Direct mutation during interval closure is rejected; split-only edits preserve current evidence and changed sample revision cannot silently inherit it. Model/migration consistency and 66-file Ruff checks passed. Workspace component tests/typecheck/build passed. The source change is awaiting independent pin-contract review and real runtime/browser qualification.


## Runtime migration and first-browser preparation

The pinned local Compose API/web/workflow images built successfully and the native migration job advanced the retained platform database to `0014_interpretation`. Existing volumes, Keycloak identity/grants, operations, and object bytes were retained. The verify-only platform smoke passed current no-grant 403, preserved object SHA-256, signed read, anonymous denial, and the exact new schema revision. Services and native worker health passed.

A 101-member synthetic draft was created before migration using references to existing qualified immutable object bytes. After migration, the authenticated in-app browser displayed draft revision 101. Through the actual membership UI it imported non-null annotation/task-schema/review/transformation content for sample `09830355-7ae0-49cc-a227-5fc240027f92`, exact revision `1212dcb6-7829-4475-8449-ba951deb9c3f`, advancing the draft to 102. The browser captured candidate `63591c4c-fc9b-4b17-b47f-909c2b0dcc1e`, generation `467f563d-c1ac-4d5f-8dee-d08bc3591c33`, for namespace `dd4a1c4a-cea4-4da4-86c3-e1c30a664539`, dataset `d0657eaa-e6aa-4ca7-b6cc-536ee5cccf72`. One resume request prepared exactly 100 members and left state preparing.

The owned API container restarted. After ordinary sign-in renewal and candidate URL reload, the browser retained the same generation, source revision 102, and prepared count 100 (`browser/resume-after-restart.png`). Independent current-authorized PostgreSQL/service inspection confirmed every original non-null pin still matched the imported content. This is restart between committed batches, not a process kill during a transaction.

The attempted independent Chrome session was paused by its extension because another extension UI was open during normal sign-in. The second-session edit/resume/seal scenario remains unqualified and is not counted as complete. Candidate preparation remains at 100 members for that test. No browser security warning was bypassed or authentication protection weakened.

A transport regression now captures via REST, prepares via authenticated MCP, and compares complete generation responses, including actual non-null evidence and the schema alias, across both transports. Invalid MCP page size is denied. OIDC protocol/JWKS are labeled fixtures in that regression; PostgreSQL and transport composition are actual. All 133 backend tests passed; 18 auth-route tests also passed separately. Twenty workspace component tests/typecheck/build passed. Final independent-session browser qualification and lifecycle review remain open.


## Overlapping seal and insert

A real PostgreSQL regression now holds an uncommitted candidate seal while a second connection attempts generation insertion. A third connection observes `pg_blocking_pids` before the seal commits; the insert then rechecks the committed state and fails with `generation no longer accepts members`. The sealed generation contains no inserted row. This proves the trigger serializes insertion against sealing, beyond sequential post-seal rejection. All 12 candidate tests passed against retained isolated `verdant_issue15_evidence_test`; Ruff passed. No production code or runtime state changed. The independent-session browser gate remains open.
