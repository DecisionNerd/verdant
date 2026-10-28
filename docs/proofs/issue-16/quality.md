# Quality policy and validation qualification

Status: core implementation verified locally on the separately owned epic-3
development stack. #16 and parent #3 are not closed. #14/#15 remain separately
owned, unfinished upstream dependencies; no change was made to their worktrees,
deployment, volumes or retained browser fixture.

Source: branch `t3/issue-3-lifecycle`, based on candidate commit
`49c047002fc4d963b0cc942b8c2b8b74ad70557b`, with the quality change in this delivery.
Accepted `main` when work started: `be4a945468775ebd879987343481cbcc322159e2`.
Migration: `0016_quality`, following `0014_interpretation` on this stack. The #14
ingestion migration predecessor still needs reconciliation by the integration owner.

## Automated proof

- Python 3.12.11, locked uv environment with Prefect 3.9.0 workflow group.
- PostgreSQL 17.6 in an isolated `verdant-epic3-quality-test` container/database,
  separate from the existing Compose stack. Focused quality/contracts/service
  tests: 57 passed. Full backend with workflow group: 178 passed.
- REST/MCP parity added for real shared policy creation, validation admission,
  checkpoint execution and exact report reads using the signed-token route fixture;
  focused auth/quality-service run: 26 passed. Provider protocol signing/session
  fixtures remain bounded test evidence, separate from real browser SSO below.
- Workspace build/typecheck passed. Quality component coverage includes authoring,
  revision chaining, strict names, failed/unknown gates, exact pinned policy,
  bounded offending metadata inspection, checkpoint/retry, candidate switching,
  read-only/denied users and input preservation. Final total: 35 workspace tests,
  including 15 quality component tests, all passed.
- Ruff lint/format, Alembic model consistency and whitespace checks passed before
  the final evidence update. Final full backend run against a fresh
  `verdant_epic3_final_test` database applied migrations from zero and passed all
  178 tests; Alembic consistency, full Ruff lint/format, 35 workspace tests,
  workspace typecheck/build and staged/unstaged whitespace checks all passed.

Important regressions: a cleanly truncated generation cannot pass; duplicate or
repeated pages cannot inflate counts; missing/empty required slices stay unknown;
integer-ratio thresholds cannot round a large count into success; interrupted
scans return no report success. PostgreSQL tests cover immutable policy/report
guards, preparing-candidate rejection, cross-dataset targets, current grants,
concurrent request/batch retries, process-independent checkpoint resume, exact old
sample metadata after draft corrections, and appended reevaluations.

Independent review found/fixed threshold rounding, scan completeness, policy-name
UI constraints and actionable exact-policy/metadata inspection. CodeRabbit CLI
authenticated but remote review failed before analysis with HTTP 403, `You are not
a member of the requested organization`; its terminal result was `Review failed:
Unknown error`. No successful CodeRabbit review is claimed, and no login, billing
or organization-setting change was attempted.

## Real browser fixture

Separate Keycloak development instance and PostgreSQL fixture, not an auth bypass:

- Keycloak image `sha256:a78d6b79572168cb25886b80a8b5b8d4ed4390743e3eecb810538683e209dd6b`.
- Synthetic realm `verdant-epic3-quality`, loopback issuer port 18792; real
  authorization-code/PKCE browser login. API port 18793, Vite port 18789.
- 101 synthetic registry revisions seeded through authorized application services.
  One has no `label`; 100 have `label: cat`. Object keys are synthetic placeholders:
  no bytes, S3 operations or artifact availability are proved by this quality test.
- Namespace `dd9dc9ed-9853-43fa-88fd-a1151a56e2d5`, dataset
  `3748e934-c3fc-47b1-9527-fedf28b1f31e`, candidate
  `e57470e5-53f2-47c6-9e51-6fd1b09a861e`, generation
  `359512a6-51b2-4114-b3b5-279b1f8d5736`, captured source revision 101.

BDD observations through the actual workspace:

1. Owner logged in through Keycloak, captured the source and prepared/sealed 101
   members in 100+1 batches. Authored `quality_ready` revision 1 with minimum 101
   members and required metadata `label`, zero allowed missing fraction.
2. Report `5dd70f32-3146-4cf9-8d16-432e774d2c5d` checkpointed 100/101 as running;
   all displayed gates remained pending. Stopped/restarted only this API process,
   reloaded the browser, selected the same durable report and resumed one member.
   Final result: size passed, metadata failed 1/101; required gates failed.
3. The offending exact revision `967b2501-8593-4f96-add7-604608539570` for sample
   `36940bec-7202-44e8-8088-9f60abffe711` showed `Metadata field “label”: missing`
   under the pinned report policy, without displaying its storage key.
4. Revised the policy to allow missing fraction 0.01. Tab navigation focused
   `Save policy revision`; Enter saved revision 2. New report
   `a11e551e-988b-4a79-9e23-15b41a40399c` completed 100+1 batches and passed both
   required rules. This is a policy-threshold correction, not a byte repair.
5. While revision 2 was selected for future validation, reopened report 1: it
   retained policy revision 1, threshold 0, and failed result. Edited a draft split
   through the UI to advance draft 101→102; the report stayed bound to source 101.
6. Independent incognito Keycloak login as the read-only fixture principal could
   inspect report 2 with no author/start/resume controls. Revoked only its isolated
   namespace grant; an immediate exact report fetch returned HTTP 403. After the
   short fixture session expired, real SSO renewal and the exact deep link showed
   `No workspace access has been granted` and `You do not have permission for this
   workspace`, with no cached report. This proves current authorized reads and
   post-renewal denial; component tests separately cover cached-content hiding on
   the polling request's 403, which was not captured before session expiry here.

Screenshots in `browser/` record checkpoint, failed rule, exact offending metadata,
passing second policy, draft advance and read-only access. Screenshots are scoped
to synthetic data; they do not establish ingestion, object retention, publication,
complete schema validation, or an enabled Evidently integration.

Readiness: the core quality slice has local implementation/integration/browser
evidence. Keep its PR draft until the owning #15 session completes and lands the
candidate baseline, then reconcile this branch with accepted main and rerun the
affected integration gates. No issue closure or default-branch delivery is claimed.

## Remaining gates

- #15 candidate acceptance/landing is owned by the existing session; its distinct
  two-session browser gate is not claimed from this isolated quality exercise.
- #14 ingestion and verified artifact/location ownership are still active work.
- #17 must enforce exact passing report/generation/policy evidence and artifact
  retention during real atomic publication; passing this UI does not publish.
- #18 exact-version consumption, lineage and availability lifecycle remain open.
- No production/cloud/provider capacity or enabled Evidently report proof.
