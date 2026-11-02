# Epic 5 acceptance evidence

Implementation checkpoint; real native browser acceptance is still running.
Do not treat this checkpoint or unit tests as native completion.

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
- Transport tests: 32 passed. Backend annotation/truth tests: initial 12 passed;
  holdout-semantics regression added afterward and must be rerun.
- Existing workspace tests: 92 passed; workspace TypeScript passed.
- Independent review findings were fixed: chain-leaf canonical head, native modifier
  provenance, malformed result types, evidence/resource bounds, cohort availability.
- Real browser reached independent Verdant HTTPS SSO and five native-verified UI
  assignments. Stock native editor acceptance remains in progress.

No original worktree, existing qualification stack, live data or GitHub issue was
modified. Failure receipts/screenshots remain in ignored workspace test-results.
