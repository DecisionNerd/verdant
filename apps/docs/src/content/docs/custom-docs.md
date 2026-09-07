---
title: Custom docs
description: Fork-friendly Starlight docs and the in-repo DocSlime tree.
---

Verdant keeps **two documentation homes** on purpose:

| Audience | Where | How you read it |
| --- | --- | --- |
| Operators & agents (how-tos) | `apps/docs/` (this Starlight site) | http://localhost:18701 or GitHub Pages |
| Maintainers & coding agents (product/engineering) | repo `docs/` (DocSlime) | In your git clone / editor |

Do **not** copy ADRs and requirements into Starlight wholesale. Keep operator pages short and
accurate; put durable decisions in `docs/engineering/adrs/`.

## Local Compose (Starlight)

Edit markdown under `apps/docs/src/content/docs/`. The `docs` service serves them at
http://localhost:18701 with `base: '/'`.

Add operator runbooks, model defaults, or house conventions alongside the upstream pages.
Update the sidebar in `apps/docs/astro.config.mjs` when you add a page.

## DocSlime tree (`docs/`)

Scaffolded and maintained with the [DocSlime](https://github.com/DecisionNerd/DocSlime) CLI:

```bash
docslime list
docslime add adr <slug>
```

Start from `docs/README.md`, then `PRODUCT.md`, `DESIGN.md`, and `engineering/`.

## GitHub Pages (optional)

This repo deploys Starlight via GitHub Actions. Forks that want public operator docs should:

1. Enable **Settings → Pages → Source: GitHub Actions**
2. Push changes under `apps/docs/**`

The workflow sets `SITE` and `BASE` from the GitHub context so forks publish to
`https://<owner>.github.io/<repo>/` without hardcoding the upstream owner.

DocSlime files under `docs/` are **not** published by that workflow; they stay with the source.
