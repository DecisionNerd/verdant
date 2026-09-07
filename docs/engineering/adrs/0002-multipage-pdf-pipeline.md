# ADR-0002: Multipage PDF pipeline (diagnose → plan → page digests → stitch)

- **Status:** Accepted
- **Date:** 2026-07-19
- **Deciders:** Verdant maintainers

## Context

Single-shot vision calls on whole PDFs lose pages, invent chrome, or blow context limits.
Operators need faithful Markdown from multipage scans (FR-4, NFR-3). Poppler can produce real
per-page rasters; the LLM must still run per content page and merge results.

## Options considered

### Option A — One vision call on the whole PDF / collage
- **Pros:** Simple.
- **Cons:** Context limits; poor fidelity; weak resume.

### Option B — Per-page digests with validate/retry + hierarchical stitch
- **Pros:** Scalable; resumable; blank pages skippable; UI can show page progress.
- **Cons:** More LLM calls; needs concurrency and stitch budgets.

### Option C — Text-only PDF extract without vision
- **Pros:** Cheap/fast.
- **Cons:** Fails on scans and diagrams — core product problem.

## Decision

We will **rasterize with Poppler (`pdftoppm`)**, **plan** blank/sparse/content pages, **digest
content pages** with validation/retry/resume, then **stitch** into `document.md` (batched when
large). Progress and plan are first-class job fields for the UI.

## Consequences

- **Positive:** Large PDFs stay memory-safe (disk-first pages); quality improves via retries
  and chrome omission prompts.
- **Negative:** Cost/latency scale with page count; stitch can still drop nuance.
- **Follow-up:** Tune `VERDANT_PAGE_CONCURRENCY`, stitch batch/char budget; expand eval fixtures.
