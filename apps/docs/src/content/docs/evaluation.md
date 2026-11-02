---
title: Quality, truth and experiments
description: Versioned quality gates, independent annotation evidence and immutable truth partitions.
---

Current quality gates evaluate an exact sealed candidate under a versioned policy.
The workspace records the report and offending revision identities, then publishes
only when the same candidate's required gates pass. Later edits or reevaluation create
new evidence; they do not alter old manifests or validation reports.

The bounded annotation task is single categorical text. Agreement reports retain
exact original annotation IDs/digests, overlap/missing/abstention/assistance provenance,
confusion/prevalence counts, observed agreement with a Wilson interval and Cohen's
kappa with explicit undefined cases. Shared-trust visibility and declared independent
work are disclosed; agreement does not prove correctness or blindness.

An admitted reviewer adjudicates disagreements with a rationale. Correction appends
new immutable canonical annotation, schema and review pins while preserving originals
and pre-adjudication reports. Truth publication binds those pins to exact sample
revisions and partitions. It checks declared groups and duplicate content, derives
validation before tuning when needed, and requires disclosure when historical test
data moves into training/validation. Fresh confirmation uses new held-out content and
groups. Retaining unchanged holdouts during a production audit is not automatically
labeled exploratory reuse.

Production audit records exact prior production membership, stratum, inclusion
probability, selection method and review provenance. Those selection facts are
operator-declared, not population-quality estimates. Evaluated flow input contains
only digest-verified source text; answer, truth, review, group and audit metadata are
excluded by the shared input-projector contract.

MLflow experiment/model authority, optional Evidently qualification and drift
comparisons remain later work. Capability discovery reports experiments as
`not_implemented`; this delivery does not fabricate experiment outcomes.
[Testing and exact proof limits](https://github.com/DecisionNerd/verdant/blob/main/docs/engineering/TESTING.md)
separate real browser/provider execution from protocol fixtures and pending live
Gemini/GLM authorization.
