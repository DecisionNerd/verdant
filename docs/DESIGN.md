# Design

Verdant’s product surfaces are the **Web UI**, **CLI**, **MCP tool responses**, and
**Starlight operator docs**. This file is the design contract for those experiences.
Visual truth for the Web UI lives primarily in `apps/web/src/app/globals.css` and the main
page layout; keep this document as rules, not a second stylesheet.

## Design Principles

- **Task over chrome** — the primary job is digest → inspect run; settings and history support
  that job, they do not compete with it.
- **Local honesty** — show real paths, statuses, and failures; never pretend cloud sync exists.
- **Agents are users** — CLI and MCP copy should be precise, stable, and path-oriented.
- **Calm greens** — brand color carries identity; semantic red/amber only for warning/error.
- **Familiar product UI** — standard controls, predictable density; surprise is reserved for
  digest quality, not novel widgets.

## Design tool context

Load `docs/PRODUCT.md` and this file before UI or docs critique. Prefer `$impeccable` (or
equivalent) against `apps/web` for visual work. Do **not** invent a parallel root
`PRODUCT.md` / `DESIGN.md`; DocSlime keeps them under `docs/`.

## Brand And Voice

- **Tone:** Direct, operator-friendly, lightly botanical (Verdant) without cute filler.
- **Terminology:** *digest* (verb/noun for a run), *run*, *job*, *page*, *stitch*, *artifact*,
  *truth fixture*. Prefer *Settings* over *Configuration* in the UI.
- **Writing rules:** Short labels; errors name the fix; empty states teach the next action;
  docs use real default ports (`187xx`).

## Visual And Content Style

- **Color:** Leaf greens on a cool green-tinted canvas (`--bg0` / `--bg1` / `--accent` in
  `globals.css`). Body text must stay high contrast (`--ink` / darkened `--muted`). Error
  accent `--accent-2` only for failures.
- **Typography:** Fraunces for display titles; Source Sans 3 for UI. Product labels stay sans;
  do not use display fonts on buttons or table data.
- **Spacing & layout:** App shell = left **sidebar** (New digest + job history + Settings)
  + main **stage** that moves compose → processing → completed/failed. Single scroll in the
  stage for Markdown (no nested preview scroller). Settings as a right **drawer**.
- **Iconography & imagery:** Prefer the Verdant logo asset; avoid sketchy decorative SVGs.
- **Content structure:** Starlight for how-tos; DocSlime for product/engineering; Mermaid in
  architecture docs instead of ASCII diagrams.

## Interaction Patterns

- **Navigation:** Top bar brand + Services; sidebar for digests and Settings; stage for the
  active compose/run only.
- **Controls:** Primary pill buttons for Digest / Test; secondary bordered for Settings,
  Refresh, Clear. Provider choice as tabs inside the settings drawer.
- **States:** Global setup alerts in an alert stack; settings feedback inside the drawer;
  run failures dismissible at the top of the run panel; jobs list for history.
- **Motion:** Short ease-out drawer enter/backdrop fade; respect `prefers-reduced-motion`.

## Components And Patterns

| Component / pattern | Use it for | Notes / source |
|---|---|---|
| Alert | Setup, settings save, digest failure | `apps/web/src/components/Alert.tsx` |
| Jobs panel | Ongoing / failed / completed history | `apps/web/src/components/JobsPanel.tsx` |
| Markdown view + TOC | Completed digest preview | `apps/web/src/components/MarkdownView.tsx` |
| Settings drawer | LLM credentials & model | Opened from sidebar foot; Esc/backdrop closes |
| Status chip | Active provider · model | Sidebar foot; opens settings |

## Accessibility

- Body text ≥ 4.5:1 contrast; do not use washed gray on tinted backgrounds.
- Settings dialog uses `role="dialog"`, `aria-modal`, labelled title; Escape closes.
- Alerts use `role="alert"` for warning/error and `role="status"` for success/info.
- Provide keyboard-reachable primary actions; keep focus styles on controls.
- Honor reduced motion for drawer and decorative transitions.

## References

- Web tokens & layout: [`apps/web/src/app/globals.css`](../apps/web/src/app/globals.css)
- Web main surface: [`apps/web/src/app/page.tsx`](../apps/web/src/app/page.tsx)
- End-user docs site: [`apps/docs/`](../apps/docs/)
- Brand mark: [`assets/verdant-logo-transparent.png`](../assets/verdant-logo-transparent.png)
  — plant in a terracotta pot, upward mouth, leaf tossing a book (served as
  `logo.png` in the web UI and docs)
- Favicons: [`assets/favicon.ico`](../assets/favicon.ico) plus size variants
  (`favicon-16.png` … `favicon-512.png`); served from `apps/web/public/` and
  `apps/docs/public/`
