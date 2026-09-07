# Journey: first digest

## Outcome

An operator brings up Compose, configures an LLM, digests a PDF or image, and reads Markdown
in the Web UI (or via MCP).

## Steps

1. `cp .env.example .env` (optional) → `docker compose up -d --build`
2. Open http://localhost:18700 — if no LLM, alert stack offers **Open settings**
3. Settings drawer → provider → test credentials → pick model
4. New digest → choose file + format → **Digest**
5. Watch plan / page progress / pipeline in the run panel
6. Preview Markdown (TOC) or HTML; download run outputs; job appears under Jobs

## Agent variant

1. Ensure MCP at `http://localhost:18702/mcp`
2. Call `digest_document` → poll `get_digest_status` → `read_output`
3. Open host-relative `data/outputs/<runId>/document.md` in the workspace

## Linked requirements

FR-1, FR-2, FR-3, FR-4, FR-5, FR-7
