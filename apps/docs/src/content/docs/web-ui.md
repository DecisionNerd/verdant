---
title: Web UI
description: Dataset curation, recoverable flows and trustworthy annotation in the workspace.
---

The React/TypeScript/Vite workspace uses TanStack Query/Table over the authorized
backend. Start with dataset discovery, then inspect metadata, source uploads, draft
members, frozen candidates, quality policies, published versions and operations.
Exact-version lineage includes a graph and a text alternative; its scope is the
loaded page, not a claim that every external lineage record is present.

## Publish and consume

Capture a candidate while the draft keeps changing. Preparation resumes from its
checkpoint and freezes sample/schema/annotation/review/split/transformation pins.
Inspect the selected and latest draft revisions, validate a policy, then publish the
passing candidate. Explicit current-draft mode conflicts if the draft advanced.

Published versions have no edit-in-place action. Exact manifests and downloads retain
their digests through archive/unarchive. Archive pauses new admission while preserving
existing checkpoints and authorized exact reads. Deletion is a separate concern.

## Run process flows

In **Process flows**, save sequential supported text steps and immutable model/flow
versions. Select a sealed candidate or exact version, start a pinned run, and inspect
attempts, native invocation and accepted step outputs. Pause/resume/cancel/retry use
durable state; refresh details after a disconnected request. A native Prefect success
alone does not establish accepted domain outputs.

Profiles disclose exact-requested identifiers or mutable aliases and report worker
credentials as unverified. Actual secrets are configured only in the worker. Paid
provider calls default to disabled; missing configuration is a visible failure.
Choose **Direct provider** or **OpenRouter** when saving a profile. OpenRouter uses
`google/…` identifiers for Gemini and `z-ai/…` for GLM, requires mutable-alias
disclosure, and pins the route in each profile version. It uses a backend OpenRouter
credential reference; no secret value belongs in the form.
The [process guide](https://github.com/DecisionNerd/verdant/blob/main/docs/engineering/PROCESS-FLOWS.md)
records limits and recovery behavior.

## Annotate and publish truth

**Annotations and agreement** creates/selects verified assignments and opens the
allowed stock-editor origin in a separate tab. Each person signs in as their own
identity. Return to import authoritative native results, inspect provenance and
agreement/disagreements, and adjudicate with an immutable rationale/review record.
Original annotations and old reports remain available after corrections.

**Truth partitions and audit** pins canonical truth/schema/review evidence to train,
validation, test or production. It derives validation from independent train groups
when absent, rejects group/duplicate leakage, and discloses exploratory test reuse.
Audited production remains production with selection stratum/probability provenance;
old truth and memberships stay immutable. These controls do not certify unseen test
labels or estimate population quality from declared sampling metadata.

Unavailable optional labeling does not block unrelated dataset work. Shared-trust
limitations and current namespace/permission context remain visible. The later
experiment engine and full production capacity are outside this delivered slice.
