# Documentation

This folder is the **DocSlime** tree: living product and engineering truth for maintainers
and coding agents. It stays in-repo so people and agents share the same evidence.

**End-user / operator how-tos** are not published from here. They live in the Starlight site:

| Surface | Location | URL (Compose defaults) |
|---|---|---|
| Operator docs (Starlight) | [`apps/docs/`](../apps/docs/) | http://localhost:18701 |
| Product & engineering (this tree) | `docs/` | Read in the git clone / editor |

See [ADR-0001](engineering/adrs/0001-docs-split-starlight-and-docslime.md).

## How the docs are organized

| Document | Question it answers |
|---|---|
| [`PRODUCT.md`](PRODUCT.md) | What is Verdant, who is it for, and why does it exist? |
| [`DESIGN.md`](DESIGN.md) | What stays consistent in UI, CLI, MCP, and docs voice? |
| [`experience/`](experience/) | Operator and agent journeys worth preserving |
| [`REQUIREMENTS.md`](REQUIREMENTS.md) | What must the system demonstrably do? |
| [`engineering/ARCHITECTURE.md`](engineering/ARCHITECTURE.md) | How is the system built? |
| [`engineering/TESTING.md`](engineering/TESTING.md) | How do we prove it? |
| [`engineering/PUBLISHING.md`](engineering/PUBLISHING.md) | How do changes reach operators and Pages? |
| [`engineering/OBSERVABILITY.md`](engineering/OBSERVABILITY.md) | How do we know digests and quality are healthy? |

Supporting folders:

| Folder | Contents |
|---|---|
| [`strategy/`](strategy/) | Optional GTM/roadmap notes (kept light for OSS) |
| [`experience/`](experience/) | Journeys and discovery notes |
| [`engineering/`](engineering/) | Architecture, CI/delivery, observability |
| [`engineering/adrs/`](engineering/adrs/) | Architecture Decision Records |

## Conventions

- **Keep docs current.** When behavior changes, update the matching DocSlime doc and the
  Starlight page operators read.
- **Link, don't duplicate.** Starlight stays short; deep design lives here.
- **Decisions are recorded.** Significant choices get an ADR.
- **No root bridge copies.** `PRODUCT.md` / `DESIGN.md` stay under `docs/` for tools that
  look there (e.g. impeccable via project config or explicit path).
