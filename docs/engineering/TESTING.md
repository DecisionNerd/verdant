# Testing

Verdant proves quality primarily through **Compose bring-up**, **TypeScript builds**, and
**truth-fixture evals** (structure + similarity) rather than a large automated unit suite
today. Gaps are listed honestly so they can be closed deliberately.

## Strategy

| Layer | What it verifies | Tools / evidence |
|---|---|---|
| Typecheck / build | Packages compile | `pnpm` filters: `@verdant/core`, `@verdant/web`, `@verdant/cli`, `@verdant/docs` |
| Image build | Docker targets produce runnable services | `docker compose build` / CI-equivalent local rebuild |
| Behavior / eval | Digest quality vs gold Markdown | `cli sync-dataset`, `cli eval`, fixtures in `data/datasets` |
| Manual operator | UI settings, jobs, preview | Local Web UI smoke after compose up |
| Unit / integration | Smallest pure logic | **Gap** — no `*.test.ts` suite checked in yet |

## Behavior coverage

| Experience / Requirement | Scenario (Given/When/Then) | Test |
|---|---|---|
| FR-1 | Given Docker, when compose up, then Web responds | Manual / compose smoke |
| FR-2 | Given Settings, when credentials test OK, then digests use provider | Manual UI |
| FR-4 | Given multipage PDF fixture, when digest completes, then stitched MD exists | Eval fixture + manual PDF run |
| FR-8 | Given `expected.md`, when `cli eval`, then structure + similarity scores | `apps/cli` eval command + Langfuse |

## Traceability contract

Product goal → `REQUIREMENTS.md` ID → eval fixture or manual scenario → (future) automated
test path. Architecture decisions that affect testability live in ADRs.

## Continuous integration gates

| Gate | Command / workflow | Blocks merge? |
|---|---|---|
| Docs site build | `.github/workflows/deploy-docs.yml` on `apps/docs/**` | Pages deploy only |
| App CI unit suite | — | **Not present yet** |

Recommended local gates before publishing images:

```bash
pnpm --filter @verdant/core build
pnpm --filter @verdant/web typecheck
pnpm --filter @verdant/docs build
docker compose up -d --build
```

## Test data and environments

- Fixtures: `data/datasets/<case-id>/{input.*,expected.md}`
- Runtime data: `./data` bind mount (gitignored contents)
- LLM keys required for live digest/eval; do not commit secrets

## Open gaps

- Automated unit tests for plan/validate/stitch heuristics.
- CI workflow for core/web/cli typecheck on PRs.
- Browser smoke for settings drawer + digest happy path.
