# Verdant

Visually rich document digestion — **image/PDF → Markdown/HTML** with Mermaid charts, artifacts, OpenObserve traces, self-hosted Trigger.dev, Langfuse evals, and an MCP server for local agents.

<p align="center">
  <img src="assets/verdant-logo-transparent.png" alt="Verdant" width="96" height="96" />
</p>

## Quick start

```bash
cp .env.example .env   # optional
docker compose up -d --build
```

Then open http://localhost:18700 → **Settings → Configure LLM**: pick **Gemini / OpenAI /
Anthropic / OpenRouter / Bedrock / Custom**. For API-key providers paste the key → **Test API
key**. For **Bedrock**, set AWS region + access key/secret → **Test credentials** (uses Converse;
prefer inference profile IDs like `us.anthropic…`). Credentials + model persist in
Turso (`data/turso/verdant.db`).

First boot pulls OpenObserve + Langfuse + builds Verdant images. The `trigger` service also clones/pins the official Trigger.dev self-host stack (large image pull the first time).

Host ports are clustered in **187xx** so they stay clear of common local servers (3000, 4321, 5432, 8000…).

**Default local logins** (no signup) — from the Web UI use **Services → OpenObserve / Langfuse / Trigger**:

| Service | URL | Credentials |
| --- | --- | --- |
| OpenObserve | http://localhost:18706 | `verdant@example.com` / `verdant1` |
| Langfuse | http://localhost:18703 | `verdant@example.com` / `verdant1` |
| Trigger.dev | http://localhost:18704 | `verdant@example.com` (magic link; Verdant opens it for you) |

| Service | URL |
| --- | --- |
| Web UI | http://localhost:18700 |
| Docs (local) | http://localhost:18701 |
| MCP | http://localhost:18702/mcp |
| Langfuse | http://localhost:18703 |
| Trigger.dev | http://localhost:18704 |
| OpenObserve | http://localhost:18706 |

Persistence:

- **`.env`** — secrets (LLM key, Langfuse/Trigger/OpenObserve)
- **`./data/`** — `inputs/`, `outputs/`, `datasets/`, `turso/` (jobs + settings DB)
- **Docker volume `openobserve_data`** — OpenObserve Parquet/meta store

PDF digests use Poppler (`pdftoppm`) for real per-page rasters, then one vision call per page
plus a text stitch pass. The Compose worker/CLI images include Poppler; for a bare-metal CLI
install `poppler` / `poppler-utils` first.

### CLI

```bash
docker compose run --rm cli digest data/inputs/sample.png --format both
docker compose run --rm cli eval --name baseline
```

### MCP (Cursor)

Prefer the always-on HTTP endpoint: `http://localhost:18702/mcp`

Stdio fallback — see [docs/MCP](http://localhost:18701/mcp/) after compose is up, or the published GitHub Pages docs after the workflow runs.

## Docs

| Audience | Where | How to read |
| --- | --- | --- |
| Operators / agents | Starlight (`apps/docs`) | http://localhost:18701 · GitHub Pages workflow |
| Maintainers / coding agents | DocSlime (`docs/`) | Start at [`docs/README.md`](docs/README.md) |

Forks customize operator markdown under `apps/docs/src/content/docs/`. Product context,
requirements, architecture, and ADRs stay in `docs/` — see
[`docs/engineering/adrs/0001-docs-split-starlight-and-docslime.md`](docs/engineering/adrs/0001-docs-split-starlight-and-docslime.md).

## Layout

```
apps/web      Next.js UI
apps/cli      CLI + MCP
apps/docs     Astro Starlight (end-user site)
docs/         DocSlime product & engineering docs
packages/core Digest pipeline
trigger/      Trigger.dev tasks
infra/        Langfuse compose + Trigger bootstrap
data/         Bind-mounted inputs/outputs/datasets
```

## License

See [LICENSE](LICENSE).
