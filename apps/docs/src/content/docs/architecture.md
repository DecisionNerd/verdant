---
title: Architecture
description: How Verdant pieces fit together for operators.
---

## Overview

Verdant is a Compose-first local app:

1. **verdant-web** — upload / jobs / preview UI
2. **verdant-worker** — processes queued digests (and hosts Trigger task definitions)
3. **verdant-mcp / cli** — same command handlers for humans and agents
4. **Trigger.dev** — optional self-hosted orchestration dashboard
5. **Langfuse** — traces, datasets, evaluation runs
6. **verdant-docs** — this Starlight site (also publishable to GitHub Pages)

## Digest flow (multipage-first)

1. Client uploads or points at a file under `data/inputs`
2. **Diagnose** — page count, then rasterize
3. **PDF → page PNGs** via Poppler (`pdftoppm`) on disk
4. **Plan** — classify blank/sparse/content pages; write `plan.json`
5. **Page work** — digest with validate/retry/resume; stream `pageProgress` to the UI
6. **Stitch** — merge page digests into `document.md` (batched for large PDFs)
7. Optional HTML with Mermaid.js under `data/outputs/<runId>/`

## Trigger vs worker queue

Trigger tasks (`digest-document`, `run-eval-experiment`) call `@verdant/core`. Digests also run
through the Compose **filesystem job queue**, so local digestion works even when Trigger
project keys are not fully bootstrapped.

## Deeper engineering docs

Authoritative product/architecture write-ups for maintainers live in the repo’s DocSlime tree
(`docs/`), especially `docs/engineering/ARCHITECTURE.md` and `docs/engineering/adrs/`. See
[Custom docs](../custom-docs/) for how that split works.
