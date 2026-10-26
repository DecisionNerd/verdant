---
title: Web UI
description: The target dataset workspace in React and Vite.
---

The target frontend uses React, TypeScript and Vite. TanStack Query manages API cache state, TanStack Table presents large collections, and React Flow renders lineage. The shared backend remains authoritative.

Navigate from dataset discovery to a workspace containing ownership/policy, drafts, published versions, samples/annotations, quality, lineage, experiments and operations. Choose an exact version to inspect reproducible content. Label Studio provides the labeling editor rather than a duplicate annotation UI.

Before publication, inspect the frozen candidate revision, schema/policy versions, quality result, splits and manifest digest. Failed gates require correction and revalidation. Published versions have no edit-in-place action: create a new draft/version.

Operation views distinguish queued/running/cancellation requested/cancelled/succeeded/failed, retain operation IDs, and show actionable failures. Reconnect by reading durable status; do not mistake a lost SSE connection for success.

All actions are keyboard reachable with clear focus, accessible dialogs/status, non-color states and a text alternative to lineage graphs. Active namespace and permission context stay visible. These are target experience requirements, not a description of the current legacy screen.

Publishing uses a sealed candidate generation and a short guarded commit. The UI must not report success from Prefect status alone: only the committed Operations result is authoritative.

Deprecation warns consumers; archive hides items from default discovery and preserves authorized exact-version reads. An archived dataset pauses new work/publication while retaining in-flight checkpoints. Unarchive restores availability. Deletion is separate. Display stale search or unavailable integrations clearly and let unrelated work continue.

Publish a selected frozen candidate while the working draft keeps changing; show both revision numbers. Only an explicit “publish current draft” action requires a latest-revision match. Workflow retries reuse completed logical effects across attempts. Publication promotes already-verified retention protection in a short transaction; bulk workers have bounded resource budgets to preserve interactive responsiveness.
