---
title: Quality and experiments
description: Versioned quality gates, annotation review, and experiment lineage.
---

Quality is attached to exact dataset content and policy versions. Evidently evaluates quality/drift; shared domain services decide whether publication gates pass. Store report bytes in object storage and report identity/results in PostgreSQL.

1. Freeze a draft candidate with exact sample/annotation revisions, splits and schema.
2. Run schema, referential, statistical and policy checks on that candidate.
3. Record the policy version, evaluated candidate, result and report artifact digest.
4. Publish only if all required gates pass for the same candidate revision. Later draft edits require validation for a new candidate but do not invalidate evidence for the frozen candidate.

Label Studio provides annotation/review workflows; its results become canonical annotation revisions with provenance. MLflow runs, metrics and models associate with exact dataset versions, without becoming the dataset registry.

Reevaluation creates a new report; it does not mutate a published version. Drift comparisons pin both evaluated and reference versions. Choose policy thresholds per workload rather than inventing global scores.

Existing document-extraction fixtures can remain focused transformation tests. They do not substitute for platform acceptance, and Langfuse is not the target platform evaluation authority. See repository `docs/engineering/TESTING.md` for the complete proof strategy.

Publication pins immutable sample metadata, label-schema revisions, review evidence and exact gate reports alongside content digests. Later reevaluation or review creates new evidence; historical interpretation remains unchanged.
