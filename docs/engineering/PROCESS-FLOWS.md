# Versioned process flows and model profiles

Issues20/21 add the dataset workspace's **Process flows** panel. Save an immutable
profile version, assign it to a model step, save a flow version, select a sealed
candidate or exact dataset version, and start a pinned run. Supported sequential
steps are identity/trim/lowercase text preprocessing and Gemini/GLM text or JSON
calls. Flow definitions cannot supply Python, URLs, executable expressions or tools.

Each flow version pins profile UUIDs and configuration digests. Each run pins its
flow, dataset, candidate, generation, optional published version and exact member
count. Existing runs retain their pins when users edit profiles, flows or drafts.
An archived input cannot admit a new run; existing committed runs remain inspectable.

## Shared services and API

`ModelProfileService` owns immutable profiles; `ProcessService` owns flow versions,
admission, actions, step acceptance and checkpoint/final acceptance. FastAPI mounts
`install_process_routes(app, sessions, Actor)`. All routes have concrete Pydantic
response models, including paginated lists and step/attempt/output lineage, for the
parent's generated SDK and FastMCP bindings. No auth implementation is added here.

- Namespace `/model-profiles`: POST/GET; `/{profile_id}` GET.
- Dataset `/process-flows`: POST/GET; `/{flow_id}` GET.
- Dataset `/process-runs`: POST/GET.
- Namespace `/process-runs/{operation_id}`: GET, with `after_sample`/`limit`.
- Namespace `/process-runs/{operation_id}/actions`: POST `{epoch, action}`;
  action is `pause`, `resume`, `cancel`, `retry` or `reconcile`.

Profile creation requires a backend environment **reference** matching
`VERDANT_MODEL_SECRET_[A-Z0-9_]+`. The actual secret is supplied only to the managed
worker by the operator. Responses omit even the reference and report `credential_status="worker_unverified"`.
The API never infers worker readiness from its own environment or receives a copy
of the worker secret. Profile digests cover the reference, fixed provider
origin, identifier, declared exact/mutable-alias identity, settings and capabilities.
Secret rotation does not change that digest; it does not establish a model snapshot.
Mutable aliases are explicitly disclosed, and observed provider model identifiers are
recorded when returned. Provider permanence cannot be independently guaranteed.

Only fixed HTTPS origins are supported: Gemini's
`https://generativelanguage.googleapis.com/v1beta` and GLM's
`https://open.bigmodel.cn/api/paas/v4`. Settings are temperature0–1, top-p(0,1], and
1–4096 output tokens; unsupported settings/capabilities fail validation. The managed
runner additionally requires `VERDANT_MODEL_CALLS_ENABLED=1`; its default is disabled.
Use a private Compose override/env file to inject operator references into **worker**
processes, never the web build or browser environment.

## Content boundary and bounded execution

`model_inputs.project_model_input(content: bytes, *, modality: str = "text") -> ModelInput`
accepts only plain, strictly decoded UTF-8 text bytes, at most64KiB without NUL.
The only output field is `text`. Callers authorize and digest-verify the exact sample
artifact first. There is deliberately no metadata, manifest, interpretation, truth,
answer, schema or review-evidence parameter. Annotation/evaluation integrations must
use this seam on sample content and keep held-out evidence outside evaluated calls.

Actual DataChain0.58.3 executes fixed domain work serially in pages of at most25
member UUIDs. Its temporary in-memory catalog holds UUIDs/booleans, never private
content or prompts. One verified artifact and the current step output are consumed
at a time. Normalized bounded outputs are immutable PostgreSQL records linked to
receipts, input/output digests, sample revisions, profile digests and accepted epochs.
Inputs remain immutable S3-compatible artifacts; outputs do not mutate source data.

A run admits at most1000 members, eight steps and1000 model calls. One fixed native
Prefect3.9.0 process deployment executes at most40 pages and checks a300-second
worker deadline before every step. A provider call has a30-second total response
budget and five-second connect/read waits; a blocked read may finish up to five
seconds after the deadline. Responses request identity encoding, reject compressed
encoding and oversized declared bodies before reads, and cap identity body bytes at
256KiB. Missing usage/cost is `null`, never zero. No paid-provider call is made by
fixture tests or ordinary disabled-worker runs.

## Recovery and ownership

The existing `OperationService` owns admission, audit/outbox, epochs and effect
receipts. `PrefectSubmission` retains exact persisted invocation/deployment/parameter
matching and lookup-before-resubmit reconciliation. Dispatcher routing uses the new
fixed `verdant-process/local-process` deployment registered alongside existing ones.
The workflow dependency group is installed only in the existing workflow image.

Pause is durable: `pause_requested` waits for an in-flight effect; `paused` fences new
effects. Resume creates a new epoch of the same logical operation. Cancellation
preserves accepted outputs and refuses to claim that an unknown effect was absent.
Confirmed protocol rejection (including exhausted429 backoff) can reset the same
receipt to prepared; explicit recovery reuses committed outputs and receipts. A
fresh trial uses a new request key/operation, with `trial_of` lineage. It can incur a
new charge; it is distinct from receipt-reusing recovery.

Ambiguous HTTP outcomes remain unknown and block replay. Native Prefect completion
can prove only workflow state, never provider-call acceptance. Reconciliation of a
final native invocation can safely reset exact pure-transform receipts; it retains
unknown model receipts. These providers do not expose a general exact-call lookup
here. Without authoritative provider evidence, an ambiguous model call remains an
explicit unresolved state; the system does not manufacture absence or repeat it.
Private payloads and credentials are omitted from ordinary error/log/telemetry paths.

## Migration and rollback

Expand with `0022_process` over `0021_availability`: add new tables and extend the
operation status constraint with pause states. Existing rows and migration files are
preserved. Deploy this schema before new API/workers; stop old dispatchers before
admitting new process kinds (old dispatchers cannot route them). Existing ingestion
and verification remain compatible. Parent integrates the separate annotation head
through a merge revision and updates `contracts.SCHEMA_REVISION` to that final head.

Rollback application code after stopping new admissions/workers leaves all process
records retained. Schema downgrade refuses populated process tables or pause states;
export/preservation and a separately authorized contraction are required before any
destructive removal. No populated downgrade or data deletion is performed here.

## Qualification

Use an isolated database ending `_test` and the owned191xx fixture:

```sh
docker compose -p verdant-process-proof -f apps/workspace/e2e/process-compose.yaml up -d --wait
scripts/test_process.sh tests/test_model_profiles.py tests/test_process_flow.py tests/test_process_memory.py -q
# Release that exact owned project before browser acceptance starts its own project.
docker compose -p verdant-process-proof -f apps/workspace/e2e/process-compose.yaml down --volumes
pnpm --filter @verdant/workspace exec playwright test --config playwright.process.config.ts
```

The browser harness uses actual Keycloak SSO, PostgreSQL, SeaweedFS, native Prefect
server/process worker, and DataChain. Provider protocol fixtures use `httpx.MockTransport`
explicitly; they are not live Gemini/GLM qualification. The browser records profile
switching and truthful disabled-provider failure without external charges.

An operator-authorized live gate is runnable with an admitted service identity and
configured profile/worker environment. This command authorizes **one accepted call**
and at most256 output tokens; the pinned profile token limit must fit the budget:

```sh
uv run --locked --group workflows python -m verdant.model_qualification \
  --namespace UUID --profile UUID --request-key unique-qualification-key \
  --authorize-charge --max-output-tokens 256
```

It uses synthetic content, emits only receipt/digest/model/usage identities and never
prints response text. Reusing a completed key reuses its receipt; uncertain keys
cannot create another paid call. Actual credentials/budget and the live gate remain
parent-owned and must be recorded separately from fixture evidence.
