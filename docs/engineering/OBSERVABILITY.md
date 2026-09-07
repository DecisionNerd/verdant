# Observability

Verdant is usually observed on a **single operator machine**. Signals should answer: is the
stack up, are digests succeeding, and is digest quality improving against fixtures?

## Observable outcomes

| Outcome / requirement | Signal | Source | Expected range | Owner |
|---|---|---|---|---|
| FR-1 stack up | HTTP 200 on web/docs/mcp/openobserve ports | curl / browser | Available after compose healthy | Operator |
| FR-3/4 digest success | Job status `completed`; `document.md` present | UI jobs + `data/outputs` | Completed without failed pages (or failures surfaced) | Operator |
| FR-4 page work | `pageProgress` + pipeline events | Web run panel / Turso `job_events` | Progress advances; failures named | Operator |
| FR-8 quality | structure + similarity scores | Langfuse experiment / eval CLI | Improving vs baseline fixture set | Maintainer |
| LLM misconfig | Setup alert / settings error | Web UI Alert | Cleared after successful test | Operator |

## Service health

| Service / journey | Indicator | Objective | Window |
|---|---|---|---|
| Web UI | Responds on `VERDANT_WEB_PORT` | Available whenever compose is up | Session |
| Worker | Jobs leave `queued` → `running` → terminal | No stuck queued jobs without logs | Per run |
| MCP | `/mcp` accepts tool calls | Agents can digest/read | Session |
| OpenObserve | UI login + ingest traces | Reachable on `OPENOBSERVE_PORT` (18706) | Session |
| Langfuse | UI login + datasets/evals | Optional; digests work without it | Session |
| Trigger | Dashboard login | Optional; digests still work via worker queue | Session |

## Telemetry design

- **Events:** Job events in Turso `job_events` (phase + message + timestamp); UI pipeline log.
  Also emitted as JSON stdout lines (`kind=job_event`) for OpenObserve log search.
- **Logs:** Docker service logs (`docker compose logs web worker openobserve`).
- **Metrics:** No Prometheus stack shipped; rely on job counts in UI and eval scores.
- **Traces (primary):** Digests emit OpenTelemetry spans to **OpenObserve** via OTLP HTTP
  (`OPENOBSERVE_URL` / `_INTERNAL`, org `OPENOBSERVE_ORG`, basic auth =
  `ZO_ROOT_USER_EMAIL`:`ZO_ROOT_USER_PASSWORD`). Root span `document-digest` with nested
  `normalize` → `plan` → `page-digest` → `page-N` → `stitch`. LLM calls are generations
  (Langfuse) or attributed spans (OpenObserve-only). Image bytes are never uploaded.
- **Traces (optional):** When `LANGFUSE_PUBLIC_KEY` / `SECRET_KEY` are set, the same process
  also exports via `LangfuseSpanProcessor` for LLM-centric eval UX.

Privacy: document bytes and prompts may be sent to the configured LLM provider; keep
`./data` and provider choice under operator control. OpenObserve/Langfuse store truncated
prompts/outputs from digest runs when tracing is enabled.

## Alerting and response

| Condition | Detect | Response |
|---|---|---|
| No LLM configured | Web alert stack | Open Settings → test credentials |
| Digest failed | Job status + run Alert | Read error; fix key/model/PDF; resume/retry |
| OpenObserve unreachable | Worker log warn; empty traces | Check compose `openobserve` volume/port |
| Langfuse unreachable | Eval/sync fallback file | Check compose langfuse; or use offline sync JSON |
| Port conflict | Bind errors on compose up | Change `187xx` vars in `.env` |

## Learning loop

1. Add or update a truth fixture under `data/datasets`.
2. Run `cli eval` after prompt/pipeline changes.
3. Compare scores in Langfuse (optional); inspect digest traces in OpenObserve.
4. If a durable design choice emerges, write an ADR.
5. Update `REQUIREMENTS.md` / Starlight only when operator-visible behavior changes.
