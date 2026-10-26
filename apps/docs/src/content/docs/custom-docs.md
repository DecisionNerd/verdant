---
title: Maintaining documentation
description: Keep target architecture and availability evidence distinct.
---

Starlight (`apps/docs/`) explains workflows to operators and agents. Repository `docs/` holds product, requirements, design, architecture, implementation plans and ADRs. Starlight is independent of the React/Vite product frontend.

Edit pages in `apps/docs/src/content/docs/`; update `apps/docs/astro.config.mjs` navigation when adding routes. Use `pnpm --filter @verdant/docs dev` to preview and `pnpm --filter @verdant/docs build` to verify. `SITE` and `BASE` configure documentation hosting.

Keep target design distinct from implemented availability. Do not publish speculative installation commands, API method names, ports or provider defaults as operational facts. Update acceptance evidence when implementation actually passes.

The ADR set contains only decisions currently in force. Add decisions with `docslime add adr <slug>` when needed, and keep the index, decision records and architecture consistent. Detailed architecture remains in the repository rather than being duplicated into every operator page.
