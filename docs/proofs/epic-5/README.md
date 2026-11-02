# Epic 5 acceptance evidence

Real browser acceptance passed on 2026-11-02 in 38.5 seconds on the retained
isolated fixture. The full run resumes previously submitted native originals
without changing their identities; failed bounded attempts remain private under
`e2e/truth/.local/failures`. No manual/browser handoff was required.

- Baseline merge `7ff335a` exactly matches `origin/main` `cf2b74c` tree
  `a58cebc5a761175a353cbee4db2424699fc32e80`.
- Immutable transport `a18e6d6ad5b64f0656a3afa9b431a277dfd031d4` was cherry-picked
  as `d3f631b`; no dirty contributor work was copied.
- Installed external native image:
  `localhost:5001/label-studio-oidc@sha256:d7f8aac5ddee20155a967d91927d7278d0ed5f0f99ccbf9fdb4818cf653a1cea`.
- External source: `cc81a17bb2a077664083979dbdbb6e685228e08f`, upstream
  `df96d79183156e556d91825d15fa7721f1145e1a` (Community 1.23.2).
- Fresh fixture: `verdant-truth-bc0aaa6b`, HTTPS identity/workspace/native/S3,
  PostgreSQL `verdant_truth_test`, owned ports 192xx.
- Transport and public-contract tests: 43 passed. Backend annotation/truth tests:
  initial 12 passed; the expanded 24-case PostgreSQL suite (holdout semantics,
  cross-role origin isolation, malformed extras, 101-ID history rotation, native
  skip/deletion and fresh confirmation) awaits the parent fresh combined-schema run.
- Full workspace suite: 96 passed, including four new annotation origin/unavailable/access
  tests. Workspace TypeScript/build and full static checks passed.
- Independent review findings were fixed: chain-leaf canonical head, native modifier
  provenance, malformed result types, evidence/resource bounds, cohort availability.
- Browser receipt: [browser-receipt.json](browser-receipt.json). It verifies two
  independent Verdant/native SSO humans, native user IDs 3/4, five tasks and ten
  actual native-export annotations, authoritative imports, agreement 4/5 (80%),
  canonical review/correction, deterministic validation derivation, production
  audit probability 0.25, unchanged test pins and retained old truth/membership.
- Screenshots: [native owner](native-owner.png), [native editor](native-editor.png),
  [agreement](agreement-browser.png), [truth audit](truth-browser.png).
- Exact agreement report: `64e88ed4-eb60-4854-bd11-4f933d31229b`, digest
  `95de459e4bbd8a2aed6cabbb8b7470b57fd56888b12381f0ec1cec68469e9781`.
- Truth before audit: `7dce13ad-bf93-4c14-bc49-33d389aec9a9`, membership revision 6.
  Truth after audit: `1bd5f774-3f05-4df5-b932-700468210b30`; exact digests are in
  the receipt. The test partition remains unchanged and nonexploratory.
- All eight owned 192xx containers were stopped after acceptance; containers,
  volumes, PostgreSQL/S3 data and private failure receipts were retained.

The browser fixture was upgraded to `0022_annotation` before the final
`machine_origin` unique constraint was added to this unmerged migration. Browser
proof therefore qualifies the application/SSO/native workflow, not that final
DDL change. The parent fresh `0023` combined migration/database proof is the
remaining authority for the final machine-origin constraint and expanded SQL
regressions. No old migration or accepted history was overwritten.

`tests/test_truth_model_input_boundary.py` is an integration gate for the
sibling-owned projector. It reads digest-verified S3 bytes and proves answer,
interpretation, review, group and audit metadata cannot enter evaluated input.
It skips in this standalone branch because the parent owns integration of
`model_inputs.py`; it must run without that skip in the combined checkout.

No original worktree, existing qualification stack, live data or GitHub issue was
modified. Failure receipts/screenshots remain in ignored workspace test-results.


The final repeatable harness is `playwright.truth.config.ts` (only `truth.spec.ts`).
Each command always starts its own retained deployment and upgrades schema, then
seeds a new dataset/native project under the existing admitted namespace/cohort.
Per-run fixture/progress/receipts are private under `.local/runs/<pid>-<time>/`;
old records are retained. Fresh checkouts derive a stable project name from their
root path; an already prepared checkout retains its recorded project name.
This repeatability change was made after the successful native run. The parent
will run the combined proof twice; this child did not restart 192xx during the
process qualification window.
