---
title: Python SDK and clients
description: Typed dataset access for automation and external clients.
---

The target programmatic interface is a generated OpenAPI client with typed Python SDK helpers. External systems use FastAPI; agents may use FastMCP. All routes share authorization and application services.

A client creates/discovers a dataset, starts an operation, follows durable status, and resolves an immutable version manifest. It verifies schema compatibility and content digests and records exact dataset-version references in experiments. Paginate metadata; process bulk data through authorized object references rather than server-local paths.

Idempotency, stale-revision errors and operation IDs must remain visible to callers. A timeout does not prove external work failed; reconcile status before submitting duplicate work. Treat signed artifact URLs as sensitive and short lived.

SDK package names, installation commands, method signatures and CLI commands will be documented after contracts are implemented. Earlier `digest`, worker and evaluation CLI commands are not the new dataset API. The retained `cli` page URL keeps existing documentation navigation working.
