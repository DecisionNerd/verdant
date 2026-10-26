# BETH: annotation agreement and cost/speed/quality tradeoffs

## Goal and status

A practitioner manages **BETH** with labeled truth data for training/testing and an unlabeled production set. They want to measure annotator agreement, resolve disagreements, and find the best cost/speed/quality tradeoffs across different process flows and model assignments.

This is a target experience and acceptance contract, not an implemented feature claim. Reuse the [ADAM comparison contract](adam-flow-model-comparison.md) for model/flow provenance, trials, caching, usage accounting, failed samples and comparable execution conditions. The [architecture](../engineering/ARCHITECTURE.md) supplies versioned datasets/annotations, shared services, durable execution and MLflow associations; agreement analysis and Pareto comparison still require implementation.

## 1. Establish BETH's data partitions

| Partition | Labels and permitted use |
|---|---|
| Truth/train | Reviewed labels for training, prompt examples and development |
| Truth/validation | A recorded, disjoint holdout from the training/development pool for flow/model/threshold selection |
| Truth/test | Frozen held-out labels for confirmation after selecting the comparison protocol and candidates |
| Production | Unlabeled inputs for operational inference, cost/latency measurement and separately designed human audits |

When initially given only train/test truth, derive validation from train before tuning. Do not repeatedly select winners on the test set and describe the resulting scores as unbiased confirmation. If a test set is used for exploration, label those results exploratory and use a fresh held-out set for confirmation. Keep the production set separate from optimization truth unless samples are explicitly labeled and assigned a new documented role.

Publish or pin exact BETH dataset versions, split membership, sample/annotation revisions and manifest digests. Partitions may be named splits or linked dataset versions with compatible schemas; no special storage topology is required. Capture source grouping/time rules and detect duplicates or related samples across splits, so pages from one document or near-duplicate records do not leak between training and evaluation. Labels and identifiers that reveal answers are excluded from model inputs on validation/test.

## 2. Obtain independent annotations

Define a versioned task/label schema, annotation instructions and review policy before comparing annotators. Use Label Studio for assignment/editing, then normalize results through Annotation Management into immutable annotation revisions with sample revision, annotator identity, task/schema version, timestamp and provenance.

Assign an overlapping set to at least two independent annotators. Show overlap coverage and assignment strategy; single-annotated samples cannot establish inter-annotator agreement. Use a representative or declared stratified overlap rather than only easy examples. Keep each original annotation separate, and avoid exposing another annotator's answer before independent submission. Model-assisted labels are explicitly marked; shared model suggestions can inflate apparent human agreement.

Missing assignments, skipped items, abstentions and “uncertain” labels remain distinct from agreement. Duplicate submissions from one annotator are revisions, not additional independent annotators.

## 3. Inspect agreement and resolve disagreement

Provide an agreement report bound to the exact sample and original annotation revisions, assignment overlap, schema and calculation version. Include a disagreement queue with side-by-side answers, sample evidence and rubric context.

| Task | Agreement view |
|---|---|
| Single categorical label | Observed agreement, confusion matrix and a suitable chance-adjusted statistic, such as Cohen's kappa for two raters |
| Multiple annotators or missing ratings | A statistic compatible with the actual design and measurement scale, such as Krippendorff's alpha where appropriate |
| Ordinal ratings | Declared ordinal distance/weighting rather than treating all disagreements equally |
| Multi-label, spans or regions | Explicit label-set overlap or matching/distance rules, including thresholds and unmatched annotations |
| Transcription/free text | Task-specific normalization and edit/structure comparison; disagreement may have multiple valid answers |

Report counts, label prevalence, per-class/slice results and uncertainty where supported. Mark undefined statistics explicitly—for example an unsuitable constant-label case—rather than reporting fabricated perfect agreement. Agreement is consistency, not proof of correctness; avoid comparing scores from different overlap sets or schemas as though they were interchangeable.

Route disputed items to an adjudicator who can accept, revise or mark unresolved with rationale. Keep original independent annotations and the pre-adjudication report intact; store adjudication as new canonical annotation/review evidence. Version instruction/schema changes and reannotate affected samples where needed. Unresolved cases stay visible and follow a declared evaluation policy: accepted alternatives, separate ambiguous slice or explicit exclusion with counts. Do not silently remove difficult cases to improve reported quality.

Publish a truth snapshot pinning the selected adjudicated revisions. Corrections create a new truth version, not changed ground truth underneath an existing experiment. Historical agreement reports remain reproducible.

## 4. Explore flows and model assignments on validation

Define flow configurations—for example A/B/C with Gemini/GLM profiles—and record the model at every step, prompts, code, runtime, preprocessing, routing/abstention thresholds and output schema. Multi-model or human-review branches are complete end-to-end configurations, not just the name of their primary model.

Evaluate the matrix on identical validation membership and truth, using the [ADAM protocol](adam-flow-model-comparison.md). Fresh repeated trials execute new measured work; recovery retries reuse receipts within their existing trial. Preserve per-sample outputs, errors, usage and timing for diagnosis.

Measure the same task quality rubric, completion/coverage, p50/p95 latency, end-to-end throughput and scoped cost. Include failed/retried calls and any human review/routing latency and cost; show processing versus evaluation/annotation costs separately. Compare at declared concurrency/cache conditions. A faster model at a worse completion rate must not appear superior because failures were omitted.

Use one declared primary quality objective for the frontier and retain secondary metrics and per-class/slice constraints. If quality is genuinely multidimensional, expose those objectives separately instead of hiding tradeoffs in an undocumented weighted score.

## 5. Inspect the Pareto frontier

For a declared workload and protocol, minimize cost and latency while maximizing quality. Configuration X dominates Y only when X is no worse on all three objectives and strictly better on at least one. The **Pareto frontier** contains configurations not dominated by another eligible configuration; a point can remain on it because it offers better quality at greater cost or latency.

Choose exact objective definitions before ranking: for example estimated processing cost per planned input sample, end-to-end sample p95 latency and task-quality score. Report cost per quality-passing sample as an additional useful metric, not a silent replacement objective. Record currency, pricing basis, accounting scope, population, time interval and quality failure treatment.

Provide interactive cost-versus-quality and latency-versus-quality views, with the third objective visible through color/labels or a table. A two-dimensional plot alone does not prove three-dimensional dominance. Each point drills into configuration, trial counts, failure coverage, agreement/truth version, per-sample results and usage receipts.

Show all comparable points and a clearly identified feasible frontier after user constraints such as minimum quality, maximum p95 latency, budget or completion rate. Apply eligibility rules consistently. Incomplete coverage or unknown costs are provisional/unrankable for claims they cannot support, not zero-cost winners. Estimated and reconciled billed-cost frontiers are distinguishable.

Display trial variability and paired uncertainty where meaningful. Label dominance computed from point estimates as provisional when uncertainty could reverse it. Do not assert statistically established dominance merely because rounded means differ. Recompute the frontier when objective definitions or constraints change and preserve the protocol used for every saved decision.

There is no universal “best.” Let the user select a frontier point based on their constraints and inspect dominated alternatives rather than hiding them. Confirm selected candidates on the held-out test set using the frozen protocol. If test outcomes lead to further tuning/selection, disclose that use and obtain fresh confirmation data.

## 6. Apply selected configurations to unlabeled production

Run chosen configurations on a pinned production version, preserving model/flow assignments and lineage. Report observed cost, speed, completion, abstentions and routing behavior. **Unlabeled production has no measured ground-truth quality score.** Validation/test quality is historical evidence for a specified population, not a production accuracy measurement.

Model-model disagreement, confidence and drift can identify samples for review, but are proxies—not truth or inter-human agreement. Keep a random/representative audit component alongside disagreement-focused sampling. Record inclusion probabilities/strata and weight estimates appropriately when claiming population quality; a hard-case review queue alone cannot estimate production accuracy.

Have humans independently annotate overlapping audit samples, measure agreement and adjudicate them using the same process. Publish newly labeled audit data as a traceable truth version. Keep it out of tuning if it is reserved for confirmation; otherwise declare its new role. Link audited production predictions to those exact labels for a separately scoped quality report.

Maintain separate validation/test and production reports/frontiers. A production cost/speed frontier can be observed without labels; a production quality frontier requires a sufficient labeled audit with its sampling/uncertainty stated. Show drift in slices, provider/model changes and evolving prices, rather than silently carrying forward an obsolete “winner.”

## Acceptance scenarios

- BETH contains pinned train, validation, test and unlabeled production partitions with duplicate/group leakage checks and no truth-label leakage into evaluated inputs.
- Two or more annotators independently label overlapping samples. Reports show actual overlap, disagreement, abstention/missingness and task-appropriate agreement, while adjudication preserves the originals.
- Correct a label or change instructions. Existing experiments/agreement reports retain old truth/schema references; new evaluation uses a new version.
- Execute three flows × two model profiles with repeated fresh trials and a recovery retry. Results preserve the ADAM accounting rules and do not count retries as independent observations.
- Construct a small test comparison where one configuration dominates another, two trade off cost/speed/quality, and one has unknown cost. The frontier excludes the dominated point, retains the tradeoff points and marks unknown-cost claims provisional/unrankable; equal points are not strictly dominant.
- Change minimum quality or maximum latency. The feasible frontier updates from the same saved results and displays the constraint/protocol changes.
- Stop one configuration early or omit usage receipts. Incomplete coverage/cost remains visible and cannot create an unsupported best result.
- Confirm selected candidates on held-out truth/test without additional tuning. A production run reports no ground-truth quality until a documented human audit supplies labels.
- Compare representative and disagreement-selected production audit slices. Population-quality claims use the recorded sampling design rather than averaging a biased review queue.
