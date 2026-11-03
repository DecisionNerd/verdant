# Issue 21: live model-profile qualification

Application source: `6703c8b6133f14c5bd9ffd7b645abcb78628c79e`.
Date: 2026-11-03. The user directed use of the existing OpenCode/OpenRouter
credential instead of obtaining new Google/Zhipu credentials.

## Actual adapter evidence

[Live receipts](openrouter-live.json) record **Verdant's** `model_qualification`
command, domain services, PostgreSQL effects and `ModelAdapter` issuing verified
HTTPS requests to `https://openrouter.ai/api/v1/chat/completions`. These are not
OpenCode-generated answers standing in for adapter execution.

| Profile/model | HTTP requests | Input/output tokens | Outcome |
| --- | ---: | ---: | --- |
| Gemini `google/gemini-3.1-flash-lite` | 1 | 18 / 2 | completed, durable receipt |
| GLM `z-ai/glm-4.5-air` | 1 | 29 / 2 | completed, durable receipt |

Both fresh trials capped output at64 tokens, used only synthetic text and disabled
reasoning/tools/fallbacks. Repeating each completed request key returned the same
operation with `reused:true` and **no HTTP request**. A transparent HTTPX transport
guard blocked any second network dispatch and recorded bounded metadata/digests;
it did not translate the wire protocol, change the origin, or mock a response.
The actual Keycloak client-credentials JWT was verified by Verdant and resolved to
an admitted service principal. The isolated local issuer used explicit insecure-local
fixture mode; provider traffic used normal verified HTTPS. PostgreSQL17.6 ran the
combined `0023_flows_truth` schema in `verdant_issue21_test`.

The existing private OpenCode OpenRouter credential was mapped to a backend
reference only in the qualification process's environment. No secret, bearer token,
private prompt or hidden reasoning is retained. Public receipts include model/route,
profile/output/request/response digests, operation/effect/dispatch IDs and provider
references. Provider-reported indicative costs were $0.00000750 and $0.00000505;
normalized product cost remains `null`, not a claim about invoice totals.

An earlier separate OpenCode SSE GLM attempt returned no visible output or usage.
It was never accepted or replayed. Its precise upstream cause remains unconfirmed.
These new trials used Verdant's nonstreaming chat protocol with `max_tokens` and
new logical request identities; they are not recovery replays of that attempt.

Native Google and Zhipu endpoints remain covered by protocol fixtures, **not**
claimed as live-qualified here. OpenRouter is now an explicit supported profile
transport. Its model identifiers and selected route are pinned, but upstream routing
and model weights remain mutable; every routed profile must disclose that fact.
This is bounded connectivity/normalization/recovery evidence, not model-quality,
capacity, JSON-generation or permanent-availability certification. Default paid
worker execution remains disabled and API readiness remains `worker_unverified`.

## UI, compatibility and adverse evidence

[Browser receipt](browser-receipt.json) and [screenshot](browser.png) come from the
real SSO/native Prefect/DataChain process test: **1 passed in1.2min**. The UI saved
native Gemini/GLM profiles, versioned them to OpenRouter Gemini/GLM profiles, pinned
all four profile digests in immutable flow versions, and reloaded the routed
version. It also retained native outputs across API restart/pause/resume and showed
disabled-provider failure/recovery/cancellation. The browser made zero paid calls.

The focused real PostgreSQL/S3/process/SDK/MCP run passed **78 tests in17.56s**.
The workspace passed **101 tests**, build/typecheck and E2E TypeScript. Ruff checked
and formatted148 Python files; generated SDK/OpenAPI drift passed. These counts
are bounded runs and are not additive to the earlier epic proof.

Regressions reject cross-family models, arbitrary origins, path traversal, false
snapshot claims, empty replies and tool calls. Existing profile definitions without
a transport retain the native default and their stored immutable digest. New route
versions change the digest without rewriting old rows. Compression/time/buffer and
uncertain-effect/revocation checks remain in the existing shared implementation.
Alembic reported no new upgrade operations. No database migration or production
configuration change was required. Independent source review found no merge-blocking
issue. CodeRabbit CLI could not review because its service returned403, “not a
member of the requested organization”; that attempt is not counted as a clean review.

## Reproduce

Run the ordinary tests and the existing browser workflow without paid credentials:

```sh
uv run --locked --group workflows pytest tests/test_model_profiles.py \
  tests/test_process_flow.py tests/test_process_mcp.py tests/test_sdk.py \
  tests/test_sdk_lifecycle.py -q
pnpm --filter @verdant/workspace test
pnpm --filter @verdant/workspace build
pnpm --filter @verdant/workspace test:e2e:process
```

Use an isolated `_test` PostgreSQL database and disposable S3 target as documented
in the [test guide](../../engineering/TESTING.md). Live calls are separate: create
an admitted service identity and an OpenRouter profile, configure its secret
reference privately in the qualification process, and explicitly authorize a new
trial. Never replay a dispatched/uncertain key to obtain fresh evidence:

```sh
uv run --locked --group workflows python -m verdant.model_qualification \
  --namespace UUID --profile UUID --request-key stable-trial-key \
  --authorize-charge --max-output-tokens 64
```
