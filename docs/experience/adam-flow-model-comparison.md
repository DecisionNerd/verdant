# ADAM: compare process flows and models

## Goal and status

A practitioner has a dataset named **ADAM** and wants to compare three process flows, changing the model used inside each flow—for example Gemini versus GLM. The experience should explain which configuration gives the best quality, speed and cost for the same task.

This is a target experience and acceptance contract, not an implemented capability claim. The [architecture](../engineering/ARCHITECTURE.md) supplies immutable dataset versions, transformation provenance, durable operations and MLflow associations. Experiment matrix, measurement and comparison behavior described here still require implementation; no additional platform is prescribed.

## 1. Select comparable inputs

Select an exact published ADAM version, manifest digest and evaluation split, not a mutable dataset name or “latest” alias. Pin sample membership/order or a recorded sampling seed, exact sample and annotation revisions, ground truth and evaluator version. Keep tuning data separate from the held-out evaluation split.

All configurations receive the same input samples. Flows may transform them differently—that is the variable being tested—but must produce outputs evaluable against the same task/schema and rubric. If outputs or tasks cannot share an evaluation contract, show separate comparison groups rather than a misleading overall ranking.

## 2. Define the matrix

| Configuration | Process flow | Model profile |
|---|---|---|
| A-Gemini | Flow A | Gemini |
| A-GLM | Flow A | GLM |
| B-Gemini | Flow B | Gemini |
| B-GLM | Flow B | GLM |
| C-Gemini | Flow C | Gemini |
| C-GLM | Flow C | GLM |

These labels are illustrative, not exact model versions. A model profile records provider, endpoint/deployment, returned model revision where available, generation settings and relevant limits. Record when a provider exposes only a mutable alias; do not claim exact reproducibility in that case.

Each configuration pins flow code/version, preprocessing, prompts/templates, tool definitions, model assignments, runtime/dependency environment and configuration digest. When a flow has multiple model steps, show the assignment at **each step**; swapping one selected step and swapping every step are distinct configurations. Model-specific settings may differ where capabilities require it; expose those differences instead of pretending the configurations are identical.

The six configurations compare end-to-end systems. Hold the flow fixed to isolate a model-profile change; hold the model profile fixed to compare flows. Other changed prompts/settings remain explicit potential confounders.

## 3. Review execution and comparison rules

Before starting, show sample count, configurations, planned repetitions, concurrency/rate-limit policy, cache policy, evaluation rubric and any available cost estimate. Estimates state assumptions and uncertainty; they are not spending guarantees. Let the user narrow the matrix or sample set and optionally set a budget or stop condition. A budget stop must be visible as incomplete coverage, including already-running work that may still incur cost.

Use repeated trials where provider nondeterminism or timing variability matters. Randomize/interleave configurations or record execution windows to reduce time-of-day/provider-load effects. Record warm-up treatment, retry policy, hardware/runtime resources and provider throttling. Equal concurrency is one test protocol, not proof of equal capacity; distinguish controlled comparisons from separately tuned throughput/cost experiments.

Make cache behavior explicit. A cold-execution comparison disables result reuse across fresh trials. A warm-cache comparison is separately labeled and records cache hits, reused stages and avoided work. Do not mix cached and uncached results into one latency/cost ranking without disclosing the mixture. Shared prepared inputs may be reused when the protocol identifies them and consistently includes or excludes their cost.

## 4. Run and inspect progress

Prefect executes long-running work; shared domain services track operations and dataset lineage; MLflow organizes experiment runs, parameters, metrics and artifacts. Provider usage receipts and timing observations must remain durable/reconcilable independently of optional telemetry delivery. Model providers still require adapters that implement the chosen contracts.

Show experiment → configuration → fresh trial → sample/step results, with corresponding operation, attempt and external run references. Preserve outputs and failure details for paired inspection. All interfaces use the same experiment identity and comparison rules.

### Fresh trial versus recovery retry

A **fresh trial** gets a distinct trial/logical operation identity and executes new measured work according to its cache policy. A **recovery retry** retains that trial's logical operation/effect identities, advances attempt fencing as needed and reuses completed receipts. Recovery never becomes an extra independent observation. Include all incurred retry work/cost in that trial and report retry counts.

An ambiguous provider call is reconciled before replay; it is not reported as free or silently duplicated. A missing usage receipt is unknown usage, not zero. Restarting the application resumes the same experiment/trials without losing completed results or executing them as new repetitions.

## 5. Compare results

Provide a table with one row per configuration, plus drill-down by trial, sample and flow step. All cells below represent measurements to collect, not example benchmark numbers.

| Result | Meaning |
|---|---|
| Coverage and completion | Planned/attempted/completed/failed/cancelled samples and trials; retries and cache hits |
| Quality | Same pinned rubric and ground truth, per-sample scores, pass threshold and aggregate results |
| Latency | Sample processing p50/p95, end-to-end elapsed time and queue delay reported separately |
| Throughput | Completed samples per measured wall-clock interval at the stated concurrency and resource conditions |
| Provider usage | Actual reported input/output/cached tokens and other billed units supported by each provider |
| Provider cost | Usage-derived estimate with pinned price source/date, currency, tier and pricing rules; reconciled billed charges shown separately |
| Other cost | Attributable compute/storage/tool charges with a documented allocation method; evaluation/judge cost separated from processing cost |
| Total cost | Sum within an explicit accounting scope, including failed calls/retries; missing components remain marked unknown |
| Cost per quality-passing sample | Total scoped cost divided by samples meeting the common quality threshold; undefined when none pass |

Use per-sample paired differences and repeated-trial summaries with variability/uncertainty where data permits. Show observation counts; do not treat retries as independent trials or repeated samples as independent new dataset members. Do not average percentile values into a claimed pooled percentile.

Keep failures in the coverage denominator and disclose the quality treatment (for example, end-to-end task failure versus quality conditional on successful output). Offer both views where useful. Never improve a score by silently excluding failed or difficult samples. A configuration with partial coverage remains partial; a common-subset comparison must identify the excluded samples and cannot replace full-coverage reporting.

Token totals alone are not a cross-provider cost comparison: billing units, tokenizers, cache discounts and model rates differ. Normalize money to a declared reporting currency with a recorded conversion basis when necessary. Distinguish costs attributed to the experiment from shared infrastructure costs and avoid double-counting a reused effect or shared preprocessing step. Missing pricing or usage makes cost incomplete, not a fabricated precise total.

## 6. Choose and preserve the decision

Allow users to inspect quality/speed/cost tradeoffs rather than declaring a universal winner. For example, compare minimum quality plus lowest cost, or minimum quality plus best latency. Save the selected configuration and comparison protocol alongside the experiment results. Outputs remain artifacts with lineage to the pinned ADAM version; creating derived datasets or publishing versions is an explicit subsequent action.

## Acceptance scenarios

- Pin one ADAM version/split and execute three flows × two model profiles. Every configuration resolves the same planned sample identities and records exact settings/differences.
- Change a model in one flow step; the resulting configuration differs only at the intended assignment and records that change in provenance.
- Advance ADAM to another version during execution. Existing trials and their evaluation inputs remain pinned to the original version.
- Run two fresh trials and force a recovery retry in one. There are two trial observations; the retried trial reuses completed effects and includes its actual retry cost.
- Restart during provider timeout/result registration. Reconcile usage/effects and resume without silently duplicating work or losing recorded costs.
- Compare warm/cold cache cases, unequal coverage and missing usage/pricing. The report identifies incomparability or uncertainty instead of displaying an unsupported winner.
- Inspect per-sample quality, measured latency, provider usage, priced estimates and reconciled charges from an aggregate row; reproduce its accounting scope and denominators.
