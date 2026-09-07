# ADR-0001: Docs split — Starlight for operators, DocSlime for product/engineering

- **Status:** Accepted
- **Date:** 2026-07-19
- **Deciders:** Verdant maintainers

## Context

Verdant needs two audiences: **operators/agents** who need how-to docs at a URL after
`docker compose up`, and **maintainers/coding agents** who need product context, requirements,
architecture, and ADRs next to the code. Putting everything only in Starlight either bloats
the public site with internal lifecycle docs or leaves agents without a stable `docs/PRODUCT.md`
contract. Relates to NFR-5 (forkable operator docs) and the DocSlime lifecycle.

## Options considered

### Option A — Starlight only (`apps/docs`)
- **Pros:** One site; simple nav.
- **Cons:** Mixes internal ADRs with getting-started; weak discoverability for design/product tools.

### Option B — DocSlime `docs/` only
- **Pros:** Strong lifecycle for agents.
- **Cons:** No polished local/Pages operator site; worse fork UX.

### Option C — Split: DocSlime `docs/` + Starlight `apps/docs`
- **Pros:** Right content in the right place; cross-links; Pages stays operator-focused.
- **Cons:** Two trees to keep consistent; must document the boundary.

## Decision

We will keep **DocSlime under repo `docs/`** for product, design, requirements, engineering,
and ADRs, and **Starlight under `apps/docs`** for end-user/operator how-tos (Compose ports,
Web UI, CLI, MCP, eval, fork docs). Starlight links to the GitHub `docs/` tree for contributor
depth; DocSlime README links to the running docs site.

## Consequences

- **Positive:** Operators get a usable site; agents get PRODUCT/DESIGN/REQUIREMENTS; forks
  customize Starlight without rewriting ADRs.
- **Negative:** Some topics (architecture overview) appear in both places at different depth —
  Starlight stays short; `docs/engineering/ARCHITECTURE.md` is authoritative.
- **Follow-up:** Keep Starlight pages accurate to current ports/UI; update DocSlime when
  behavior changes in the same PR when practical.
