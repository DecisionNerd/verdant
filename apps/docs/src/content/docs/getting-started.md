---
title: Getting started
description: Bring up Verdant with Docker Compose.
---

## Prerequisites

- Docker + Docker Compose
- A vision-capable LLM: Gemini, OpenAI, Anthropic, OpenRouter, **AWS Bedrock**, or any
  OpenAI-compatible endpoint ([Configuration](../configuration/))

## Start

```bash
cp .env.example .env   # optional defaults
docker compose up -d --build
```

Open the Web UI → **Settings** → configure your LLM (saved to `data/config.json`). You can
still set `LLM_API_KEY` in `.env` instead; the UI key takes precedence for API-key providers.

### URLs

| Service | URL | Login |
| --- | --- | --- |
| Web UI | http://localhost:18700 | — |
| Docs (this site) | http://localhost:18701 | — |
| MCP | http://localhost:18702/mcp | — |
| Langfuse | http://localhost:18703 | `verdant@example.com` / `verdant1` |
| Trigger.dev | http://localhost:18704 | `verdant@example.com` (instant magic link) |
| OpenObserve | http://localhost:18706 | `verdant@example.com` / `verdant1` |

All host ports sit in the **187xx** cluster so they do not collide with typical local servers.

No signup step — OpenObserve and Langfuse seed that user on first boot; Trigger local mode
signs in via magic link (printed to container logs).

From the Web UI, use **Services → OpenObserve / Langfuse / Trigger**. If auto-login fails,
open **Services → All services** for credentials and manual links.

## Persist data

Host directory `./data` is bind-mounted:

- `data/inputs` — uploads
- `data/outputs` — digest results (`document.md`, `pages/`, `artifacts/`, …)
- `data/datasets` — truth fixtures
- `data/turso/verdant.db` — jobs, settings, reviews (libSQL / Turso)

OpenObserve persists in the Compose named volume `openobserve_data`.

Secrets live in root `.env` (gitignored).

## First digest

**Web:** see [Web UI](../web-ui/).

**CLI:**

```bash
cp your-scan.pdf data/inputs/
docker compose run --rm cli digest data/inputs/your-scan.pdf --format both
```

PDFs are rasterized with Poppler and digested **one page at a time**, then stitched with a
deterministic code join by default (`VERDANT_STITCH_MODE=code`) so the final Markdown is not
bounded by LLM context. Set `llm` for hierarchical LLM stitch. Results
appear under `data/outputs/<runId>/`.

**Agent:** [MCP for agents](../mcp/).
