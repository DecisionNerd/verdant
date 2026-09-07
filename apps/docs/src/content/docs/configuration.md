---
title: Configuration
description: Providers, API keys, and ports.
---

## LLM providers (Web UI)

Open **Settings** in the sidebar and pick a provider tab in the drawer:

| Provider | Auth | Notes |
| --- | --- | --- |
| Gemini | API key | AI Studio / OpenAI-compat → **Test API key** |
| OpenAI | API key | Lists models from `/v1/models` |
| Anthropic | API key | Native Messages API + model list |
| OpenRouter | API key | OpenAI-compatible |
| Bedrock | AWS access key + secret + region | Converse API; inference profile IDs preferred |
| Custom | API key + base URL | Any OpenAI-compatible base URL |

Each provider stores its own credentials and **selected model** in `data/config.json` (gitignored). Switching tabs switches the active provider for digests.

Flow:

1. Select a provider
2. Paste credentials (base URL only for Custom; region + AWS keys for Bedrock)
3. **Test** — verifies, saves, then loads available models / inference profiles
4. Pick a model from the dropdown (persists per provider)

### Bedrock quirks

- Auth is **SigV4 AWS credentials**, not a Bedrock-specific API key
- Many Claude models need a **cross-region inference profile** ID (`us.anthropic…`) rather than bare `anthropic.*`
- Enable model access in the Bedrock console for the chosen region
- Worker can also use `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` (or the default AWS credential chain)

Precedence for API-key providers: UI saved key → `LLM_API_KEY` in `.env`.  
For Bedrock: UI keys → `AWS_*` env → default credential chain.

## Optional `.env` fallback

```bash
cp .env.example .env
```

| Variable | Purpose |
| --- | --- |
| `LLM_API_KEY` | Fallback key for the active provider if UI has none |
| `LLM_BASE_URL` | Fallback for Custom only |
| `LLM_MODEL` | Fallback model for the active provider |
| `AWS_REGION` | Bedrock region fallback |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Bedrock credential fallback |
| `AWS_SESSION_TOKEN` | Optional STS token for Bedrock |

## Services

| Variable | Default |
| --- | --- |
| `VERDANT_WEB_PORT` | `18700` |
| `VERDANT_DOCS_PORT` | `18701` |
| `VERDANT_MCP_PORT` | `18702` |
| `LANGFUSE_PORT` | `18703` |
| `TRIGGER_HOST_PORT` / `TRIGGER_API_URL` | `18704` / `http://localhost:18704` |
| `LANGFUSE_MINIO_PORT` | `18705` |
| `OPENOBSERVE_PORT` / `OPENOBSERVE_GRPC_PORT` | `18706` / `18707` |
| `LANGFUSE_BASE_URL` | `http://localhost:18703` |
| `OPENOBSERVE_URL` | `http://localhost:18706` |
| `TURSO_DATABASE_URL` | `file:/data/turso/verdant.db` |

OpenObserve root user (Compose defaults):

- Email: `verdant@example.com`
- Password: `verdant1`

Langfuse is auto-initialized via `LANGFUSE_INIT_*` vars in `.env.example` with a default local user:

- Email: `verdant@example.com`
- Password: `verdant1`

Trigger.dev local stack (`./infra/trigger/up.sh`) sets `NODE_ENV=development` so magic-link login with `verdant@example.com` is printed to the webapp container logs (no inbox). That address is promoted to admin via `ADMIN_EMAILS`.

### One-click from the Web UI

The Verdant top bar **Services** menu opens OpenObserve, Langfuse, and Trigger:

- **OpenObserve** — redirects to the local UI (root user above).
- **Langfuse** — Verdant completes NextAuth credentials for the seeded user, sets the session cookie, then redirects.
- **Trigger** — Verdant requests a magic link and redirects your browser to it (reads the link from Trigger webapp logs via the Docker socket mounted read-only on `web`).

Fallbacks and credentials: http://localhost:18700/services

## PDF / multipage tuning

Optional worker env (see `.env.example`):

| Variable | Role |
| --- | --- |
| `VERDANT_PDF_DPI` | Raster DPI for `pdftoppm` |
| `VERDANT_PAGE_CONCURRENCY` | Parallel page digests |
| `VERDANT_EXTRACT_MODE` | `ir` (default) dual-stream keep+discard JSON; `markdown` legacy Markdown-only |
| `VERDANT_STRUCTURE_LLM` | `1` (default) run LLM structure pass from discard+keep briefs; `0` skips |
| `VERDANT_STITCH_MODE` | `code` (default) deterministic join; `llm` hierarchical LLM stitch (markdown extract only) |
| `VERDANT_STITCH_BATCH` | Pages per stitch batch (LLM mode) |
| `VERDANT_STITCH_CHAR_BUDGET` | Approx. char budget per stitch call (LLM mode) |
