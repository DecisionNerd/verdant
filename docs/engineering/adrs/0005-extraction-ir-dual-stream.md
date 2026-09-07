# ADR-0005: Dual-stream extraction IR (keep + discard)

- **Status:** Accepted
- **Date:** 2026-07-20
- **Deciders:** Verdant maintainers
- **Depends on:** [ADR-0004](0004-code-first-stitch-and-ir.md)

## Context

Page digests today emit only “good” Markdown and ask the model to *omit* chrome. Omission is
silent: residual page numbers, running headers, and branding still leak into joins, and the
assembler has no fingerprint of what was rejected. For code-first stitch to clean seams without
an LLM merge, **discarded material must be extracted explicitly**.

## Decision

Page vision extraction produces a **dual-stream IR** (`PageIr`):

1. **`keep`** — substantive content blocks that may enter the final document.
2. **`discard`** — every low-information / chrome / layout artifact the model sees, typed and
   short-texted so it *never* compiles into `document.md` but *is* available for assembly logic,
   Review pages, and cross-page chrome fingerprinting.

Nothing visible on the page is left unclassified: every region maps to exactly one of `keep` or
`discard`. Ambiguous cases go to `keep` with `uncertain: true` (or a small optional checker),
never silent drop.

Final document compile is **code-only** over `keep` (plus seam rules driven by `discard`
fingerprints). LLMs may run on page extract and tiny checkers, not whole-document stitch.

## Discard taxonomy (must extract)

These are first-class `discard` kinds — extract short `text` (or image note), never body prose:

| Kind | Examples |
| --- | --- |
| `page_number` | `12`, `Page 12 of 40`, `12 / 40` |
| `running_header` | Repeated chapter/section title at page top |
| `running_footer` | Copyright strip, confidentiality line at bottom |
| `folio` | Decorative folio marks, rules used only as page chrome |
| `branding` | Logos, wordmarks, watermarks, vendor icons on every page |
| `decorative` | Rules, borders, background patterns with no semantics |
| `layout` | Margin/column/gutter notes the model is tempted to describe |
| `nav_chrome` | TOC sidebars, “continued…”, binder tabs (non-body) |
| `blank_region` | Large empty bands (helps blank-page / sparse checks) |
| `other_chrome` | Fallback — still captured with short text |

Assembler uses discard streams across pages to build a **chrome fingerprint** (normalized text +
kind + zone). Text matching that fingerprint at page edges of `keep` is stripped before join.

## Keep taxonomy

| Kind | Role |
| --- | --- |
| `heading` | ATX level 1–6 + text |
| `paragraph` | Body prose (may carry `continues: true` at page edge) |
| `list` | Ordered/unordered items |
| `table` | Compact row/col JSON or GFM string |
| `callout` | Note/warning boxes |
| `figure` | Caption + artifact placeholder |
| `mermaid` | Diagram source |
| `math` | Inline/block LaTeX |
| `code` | Preformatted / source |
| `caption` | Standalone caption not tied to a figure yet |

## Wire format (token-efficient)

Per page artifact: `data/outputs/<runId>/pages/page-NNN.ir.json` (+ optional `.md` preview compiled
from `keep` for humans).

Short keys, enum `k` for kind, zone abbrev, no prose commentary:

```json
{
  "v": 1,
  "page": 12,
  "pages": 40,
  "keep": [
    {
      "id": "k1",
      "k": "heading",
      "lvl": 2,
      "t": "Installation",
      "z": "body",
      "edge": "top"
    },
    {
      "id": "k2",
      "k": "paragraph",
      "t": "Run the installer…",
      "z": "body",
      "cont": "start"
    }
  ],
  "discard": [
    { "id": "d1", "k": "running_header", "t": "Playbook · Install", "z": "top" },
    { "id": "d2", "k": "page_number", "t": "12", "z": "bottom" },
    { "id": "d3", "k": "branding", "t": "ACME logo", "z": "top" }
  ]
}
```

### Field cheat sheet

| Field | Meaning |
| --- | --- |
| `v` | Schema version |
| `page` / `pages` | Index (1-based) / total |
| `keep[]` / `discard[]` | Dual streams |
| `id` | Stable within page (`k*` / `d*`) |
| `k` | Kind enum (see taxonomies) |
| `t` | Text (discard: short; keep: full transcription) |
| `z` | Zone: `top` \| `body` \| `bottom` \| `margin` \| `overlay` |
| `lvl` | Heading level (keep/heading only) |
| `edge` | `top` \| `bottom` \| omit — touches page boundary |
| `cont` | `start` \| `mid` \| `end` — split across pages |
| `uncertain` | `true` if classification was soft |

Tables/lists use compact nested fields (`rows`, `items`) rather than Markdown-in-JSON when cheaper.

## Compile rules (code)

1. **Never** emit `discard` into `document.md` / HTML.
2. Build chrome fingerprints from `discard` across ≥2 pages (exact + folded whitespace +
   lowercase).
3. Drop leading/trailing `keep` blocks on a page when normalized `t` matches a fingerprint and
   `z`/`edge` is page chrome zone.
4. Join `keep` in page order; merge `cont` pairs across page boundaries by code.
5. Rebase heading levels with a deterministic stack (optional tiny LLM checker only if stack
   conflicts).
6. Optional Review UI: side-by-side raster / keep / discard.

## LLM roles

| Stage | Model? | Note |
| --- | --- | --- |
| Page → `PageIr` | Yes (vision) | Must fill **both** streams |
| Uncertain classify | Optional small call | Single block only |
| Chrome fingerprint / join / heading rebase | No | Code |
| Whole-document stitch | No | Forbidden as default |

## Consequences

- **Positive:** Chrome is evidence, not hope; assembler can strip repeats without context caps;
  Review pages can show what was rejected.
- **Negative:** Page extract prompts/schema validation are stricter; tokens spent on short discard
  text (intentional and bounded).
- **Migration:** Until IR lands, Markdown-only digests remain; when IR is present, compile from
  `keep` and treat `.md` as a derived preview.

## Open follow-ups

- Zod schema + validate in `@verdant/core`
- Prompt rewrite: “extract discard stream exhaustively; never omit chrome silently”
- Compiler + fingerprint unit tests on fixtures with known running headers
