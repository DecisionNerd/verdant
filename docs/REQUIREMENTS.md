# Requirements

These requirements describe what a Verdant deployment must demonstrably do for local
operators and MCP agents: bring up the stack, configure an LLM, digest documents (including
multipage PDFs), inspect outputs, and evaluate against truth fixtures.

## Functional requirements

| ID | Requirement | Derived from | Acceptance behavior |
|---|---|---|---|
| FR-1 | The system shall start Web, Docs, MCP, worker, OpenObserve, Langfuse, and Trigger via Docker Compose with documented host ports. | PRODUCT goals | Given a machine with Docker, when `docker compose up -d --build` succeeds, then the Web UI responds on the configured web port (default 18700). |
| FR-2 | The system shall let an operator configure an LLM provider (Gemini, OpenAI, Anthropic, OpenRouter, Bedrock, or Custom) and persist credentials/model under `data/config.json`. | PRODUCT goals | Given Settings is open, when credentials are tested successfully, then digests use that active provider/model on subsequent runs. |
| FR-3 | The system shall accept PNG, JPEG, WebP, or PDF input and write run outputs under `data/outputs/<runId>/`. | PRODUCT problem | Given a supported file, when a digest completes, then `document.md` (and optional HTML) exist under that run directory. |
| FR-4 | For PDFs, the system shall diagnose pages, skip near-blank pages per plan, digest content pages with validation/retry, and stitch into a single document. | PRODUCT vision | Given a multipage PDF, when digest finishes, then `plan.json`, per-page `pages/page-NNN.md`, and stitched `document.md` exist; blank-only pages are not required as content digests. |
| FR-5 | The Web UI shall show job history and reopen a selected run’s status, pipeline events, and Markdown/HTML preview. | DESIGN task-over-chrome | Given a completed job, when the operator selects it, then the run panel shows status and preview content for that `runId`. |
| FR-6 | The CLI shall expose digest, status, outputs, read, dataset sync, eval, worker, and MCP commands runnable via Compose. | Audience: operators | Given Compose is up, when `docker compose run --rm cli digest …` succeeds, then outputs appear on the host `data/` mount. |
| FR-7 | The system shall expose an always-on MCP HTTP endpoint that can digest, poll status, list/read outputs, sync datasets, and run evals. | Audience: agents | Given MCP is up, when a client calls the documented tools, then responses include host-relative paths under `data/outputs/…`. |
| FR-8 | The system shall support truth-fixture evaluation (structure + similarity scores) using `data/datasets` and Langfuse when configured. | PRODUCT quality stance | Given a fixture case with `expected.md`, when `cli eval` runs, then a scored experiment result is produced (Langfuse or offline sync artifact). |

## Non-functional requirements

| ID | Quality attribute | Target / constraint | Why it matters |
|---|---|---|---|
| NFR-1 | Local-first data | Inputs, outputs, datasets, and UI config persist under `./data` on the host | Operators keep documents off third-party digesters by default |
| NFR-2 | Port isolation | Default host ports clustered in `187xx` and overridable via `.env` | Avoid collisions with common local servers |
| NFR-3 | Multipage memory | PDF page rasters are disk-first; pages loaded as needed, not all base64 at once | Large PDFs must not OOM the worker |
| NFR-4 | Accessibility (Web) | Contrast and dialog/alert semantics per `DESIGN.md` | Operators use the UI under varied lighting and assistive tech |
| NFR-5 | Forkable docs | Starlight builds with configurable `SITE`/`BASE` for GitHub Pages | Forks can publish their own operator docs |

## Behavior trace

| Requirement | Given | When | Then |
|---|---|---|---|
| FR-2 | Web UI is open and no LLM is configured | Operator opens Settings, tests a valid Gemini key, picks a model | Status chip shows provider · model; Digest enables when a file is chosen |
| FR-4 | A multipage PDF is queued | Worker processes diagnose → plan → pages → stitch | UI shows plan summary and page progress; final Markdown preview loads |
| FR-7 | MCP HTTP is listening | Agent calls `digest_document` then `read_output` | Agent receives `runId` and can read `data/outputs/<runId>/document.md` |

## Constraints & assumptions

- **Constraint:** A vision-capable LLM endpoint is required; Verdant does not ship model weights.
- **Constraint:** PDF page rasters depend on Poppler (`pdftoppm`) in worker/CLI images.
- **Assumption:** Operators run Docker Compose on macOS/Linux with bind mounts for `./data`.
- **Assumption:** Langfuse default seed user exists only on first empty DB init.

## Dependencies

- Docker / Docker Compose
- Chosen LLM provider APIs (or AWS Bedrock Converse)
- Poppler in worker/CLI images
- Optional: self-hosted Trigger.dev stack (`infra/trigger`), Langfuse compose (`infra/langfuse`)

## Open questions

- Whether Trigger dashboard runs should become mandatory for digests (today the filesystem
  worker queue is sufficient) — maintainers.
- Public SemVer / release channel for CLI images beyond Compose tags — maintainers.
