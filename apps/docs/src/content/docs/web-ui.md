---
title: Web UI
description: Digest documents, watch jobs, and preview Markdown in the browser.
---

Open **http://localhost:18700** after `docker compose up`.

## Layout

| Area | Purpose |
| --- | --- |
| **Top bar** | Brand + **Services** (one-click Langfuse / Trigger) |
| **Sidebar** | **New digest**, job history, and **Settings** |
| **Stage** | Compose → processing → completed or failed |
| **Settings drawer** | LLM provider credentials and model (from sidebar) |

## Flow

1. Click **New digest** in the sidebar.
2. If you see **LLM not configured**, open **Settings** and test credentials.
3. Drop a PNG, JPEG, WebP, or PDF, pick a format, click **Digest**.
4. The stage switches to an animated **processing** view (plan, page dots, pipeline).
5. On success, Markdown (or HTML) fills the same stage — one scroll, contents in a collapsible section.
   Format tabs sit on the left of the stage bar; **Review pages** and **Download** on the right.
   If HTML was not generated with the digest, the HTML tab is grayed out; use **Render HTML** to
   convert `document.md` locally (no re-digest).
6. Past runs stay in the sidebar; select one to reopen it.
7. If a digest fails (for example LLM rate limits), use **Retry job** — completed pages resume from disk.
8. **Review pages** opens `/runs/<runId>/pages` — each page’s raster beside its raw page digest
   (useful when the stitched document looks shorter than the source).

Uploads land in `data/inputs`; outputs live under `data/outputs/<runId>/`.

## Failures

- The pipeline log lists page-level errors (including HTTP 429 from providers).
- Local digests use the worker queue, not Trigger.dev — Trigger is an optional dashboard.
- Digests are traced in Langfuse as `document-digest` chains (open **Services → Langfuse**).
  Each run nests normalize / plan / page-digest / stitch spans and per-call generations.

## Settings tips

- Each provider keeps its own key/model in `data/config.json`.
- **Bedrock** uses AWS access key + secret + region (Converse API); prefer inference profile IDs
  like `us.anthropic…`.
- Escape or the backdrop closes the drawer.
- Details for every provider: [Configuration](../configuration/).
