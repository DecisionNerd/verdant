# Quality policies and candidate validation

The dataset workspace authors immutable policy revisions and evaluates sealed
candidate generations through shared REST/MCP/managed-Python quality services.
Reports retain the exact candidate, generation, source revision, policy revision
and evaluator version. Reevaluation creates another report; changing a policy or
the working draft never replaces earlier evidence. This implements the core
quality slice of #16 within lifecycle epic #3. Publication enforcement and
artifact retention are implemented by the separate
[publication service](IMMUTABLE-PUBLICATION.md); a passing report alone does not publish.

`QualityDefinition` version 1 has 1–32 uniquely named rules and at least one
required rule. Each rule applies to the whole generation or one named split:

- `minimum_samples`: require a positive minimum member count.
- `required_metadata`: require a top-level sample-metadata field, allowing an
  authored maximum missing fraction between 0 and 1. Absent keys and JSON null
  are missing; false, zero and empty strings are present. This checks presence,
  not a general JSON Schema or the meaning of an annotation.

Metadata rules over an empty slice return `unknown`; minimum-count rules fail.
Any failed or unknown required rule makes `required_rules_pass` false. Optional
rules remain visible but do not override required results. Decimal threshold
comparisons use exact integer ratios and do not depend on decimal-context precision.

The application service reads exact sample revisions from a sealed generation,
ordered by ascending unique sample UUID, and its sealed member count. The core
evaluator rejects duplicate/out-of-order membership
and count mismatches, and propagates interrupted reads. It stores exact counts
and at most 0–100 offending sample/revision identities per rule (default 25),
marking truncated details explicitly. It does not copy private metadata or object
keys into its result. Memory is bounded by the rule/detail limits and one input
member. `QualityService.advance` reads at most 500 members per command (UI batches
100), checkpoints counters/cursor and bounded evidence in PostgreSQL, and exposes
all rule statuses as pending until the full sealed count is evaluated. Unknown
or failed required rules never become passing gates. A request may resume after
process restart; retrying an earlier checkpoint reuses committed progress.

Every command/read reloads current namespace grants before returning records or
advancing work. Read-only users inspect evidence but cannot author or advance it.
Dataset-scoped policy revision creation uses an expected prior revision; request
identities make policy/report admission retries idempotent. PostgreSQL guards
reject policy mutation, report identity changes, deletion and completed-report
updates. New reports must bind an exact sealed candidate and policy in the same
dataset. Each accepted authoring/start/batch command writes durable audit;
the shared `quality` observation path records command outcomes independently.

In the workspace, select a frozen candidate, author/select a policy revision,
start validation, then resume bounded batches. Inspect the report's pinned policy
and offending exact revisions, including the relevant metadata field. The current
validation-policy selector does not change a report's pinned policy. Presence
checks are the supported schema rules; arbitrary JSON Schema, statistical drift
and annotation-review validation are not implemented by this evaluator.

REST routes under `/api/v1/namespaces/{namespace}/datasets/{dataset}`:

- `GET/POST /quality/policies`, `GET /quality/policies/{policy}`.
- `GET/POST /candidates/{candidate}/quality/reports`.
- `GET /quality/reports/{report}`, `POST /quality/reports/{report}/evaluate`.

The matching MCP tools are `quality_policy_create`, `quality_policies_list`,
`quality_policy_get`, `quality_validation_start`, `quality_reports_list`,
`quality_report_get` and `quality_validation_advance`. Managed Python callers use
`QualityService` with their verified principal and the same bounded contracts.

Evidently is not enabled or installed in this slice. No provider report/artifact
or reference-data comparison is claimed. An enabled adapter must retain exact
versioned artifacts and reference-data identity and must preserve failed/unknown
required gates on errors. Dashboard availability is independent of these core
checks. A passing core report alone never establishes publication readiness.

The publication development stack reconciles candidate `0014_interpretation`,
ingestion `0015_ingestion`, quality `0016_quality` and publication `0017_publication`.
Reconcile accepted dependency history before landing. Rollback of application code can retain these
additive tables; an explicit schema downgrade deletes policies/reports and needs
a backup and deliberate operator decision. Startup never upgrades schema.

See [quality qualification](../proofs/issue-16/quality.md) for exact environment,
unit/PostgreSQL/transport/browser evidence and remaining dependency/provider gates.
