# ADR-0004: Code-first stitch and extraction IR direction

- **Status:** Accepted
- **Date:** 2026-07-20
- **Deciders:** Verdant maintainers

## Context

Hierarchical LLM stitch condensed multipage digests severely (e.g. ~125k page chars →
~30k stitched chars) and is bounded by model context. Operators need the final document to
scale with page count. HTML preview should not require a full re-digest.

## Options considered

### Option A — Keep LLM hierarchical stitch as default
- **Pros:** Cleaner reading flow possible.
- **Cons:** Context ceilings; content loss; opaque merges.

### Option B — Deterministic join of page digests (default); optional LLM stitch
- **Pros:** No context window; lossless relative to page digests; fast; resumable.
- **Cons:** Residual page-edge seams until a structured IR cleans them.

### Option C — Structured extraction IR → code assemble → optional LLM check
- **Pros:** Token-efficient page payloads; assembly algorithm handles hierarchy; LLM only on
  small checks.
- **Cons:** Needs schema + pipeline work beyond a single change.

## Decision

1. **Default stitch mode is code** via `VERDANT_STITCH_MODE=code` (deterministic join of
   validated page Markdown). Set `llm` to restore hierarchical LLM stitch.
2. **HTML** for an existing run is produced by `markdownToHtmlDocument` only
   (`POST /runs/:runId/render-html`) — no vision re-digest.
3. **Follow-up:** Dual-stream extraction IR ([ADR-0005](0005-extraction-ir-dual-stream.md)) —
   page vision must extract both `keep` and `discard` (chrome); compile final docs in code from
   `keep` only, using `discard` for fingerprints / seam cleanup.

## Consequences

- **Positive:** Final Markdown length scales with pages; HTML-on-demand is cheap.
- **Negative:** Code join may leave minor page-boundary seam noise until IR lands.
- **Supersedes in practice:** ADR-0002’s implication that LLM stitch is the primary merge path;
  page raster → per-page digest remains.
