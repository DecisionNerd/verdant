# Python SDK and MCP contracts

`verdant.sdk.Client` is shipped with `verdant-platform`. It calls the same current
namespace-authorized API used by the workspace. Generated method signatures and
return values use the platform's Pydantic contracts; the SDK does not duplicate
domain rules or call database/worker internals. Public OpenAPI is retained at
`docs/contracts/openapi.json`.

Run `uv run python scripts/generate_sdk.py` after changing public routes/contracts.
`--check` fails on SDK or OpenAPI drift and runs in platform CI. Generation is offline:
it uses a disconnected engine and cannot load runtime identity/storage credentials.

## Authentication and compatibility

Pass a current Keycloak access token or a callback that returns one. Tokens require
the fixed issuer, API audience, `verdant` scope, supported signature/expiry and an
explicitly admitted OAuth client. `VERDANT_SERVICE_CLIENTS` resolves only provisioned
service principals. Optional `VERDANT_HUMAN_CLIENTS` resolves only previously admitted
human issuer/subject principals. The client lists must be disjoint. Human access does
not turn email into identity or permit a service-client token to impersonate a human.
Neither transport provisions an identity from an arbitrary bearer token.

HTTP and MCP use the same verifier, exact subject and current-grant authority. They
record `http-bearer` and `mcp` attribution; clients cannot set their own attribution
with an `X-Transport` header. Browser cookie sessions continue to require origin and
CSRF checks. Grant revocation/disablement is checked on subsequent reads/commands.

API responses carry `X-Verdant-Contract-Version`; unsupported or absent versions fail
closed in the SDK. `/api/v1/capabilities`, MCP `platform_capabilities`, and authenticated
resource `verdant://capabilities` expose delivered contract versions and honest domain
support. Experiment authority belongs to its later epic and is explicitly reported
as `not_implemented`; no fake experiment creation/status is exposed.

## Requests, pages and recovery

Generated methods accept keyword arguments and typed command models, for example
`create_dataset(namespace_id=..., command=DatasetCreate(...))`. Shared model limits
remain authoritative. `iter_pages(sdk.list_datasets, namespace_id=..., limit=25)` yields
typed bounded pages, detects repeated cursors and has a finite page budget.

Only GET requests retry transient transport/429/502/503/504 failures, at most twice.
Commands are never automatically replayed after a lost response. Reconcile by
resubmitting the same persisted domain request key where the command supports it;
a fresh-trial request uses a distinct key. `wait_operation` has a finite polling
budget and returns terminal, paused or reconciliation-required state without claiming
success. Budget exhaustion never cancels or retries work. `SDKError` carries public
bounded detail, HTTP status and read-retry eligibility without request credentials.

## Exact artifact consumption

Artifact identity is logical ID, SHA-256 and byte count. Provider endpoints, signed
URLs and server-local files are not canonical references. Configure exact operator
storage origins separately with `storage_origins=("https://objects.example",)`;
locations are resolved through the authorized application download path. Only one
redirect to a configured origin is accepted. A separate HTTP client sends no platform
bearer token or platform cookies to object storage, including a same-host redirect.
HTTP is allowed only for explicitly enabled loopback fixtures.

`download_verified(descriptor, output)` streams at most the platform's 256 MiB artifact
limit through a bounded spool, checks exact byte count/digest, then writes verified
bytes to the caller's output. Failed verification leaves that output untouched.
`verified_manifest(namespace_id=..., dataset_id=..., version_id=...)` also checks the
version/descriptor identity, supported schema, namespace/dataset/candidate/generation
and strictly ordered distinct members. Its typed in-memory reader supports 16 MiB;
larger manifests remain available through the verified streaming method. JSON API
responses are limited to 64 MiB. Ordinary HTTP logs redact signed query capabilities.

## Runnable examples and proof

`uv run python examples/sdk_consume.py NAMESPACE DATASET VERSION` consumes and verifies
an exact manifest. `examples/mcp_consume.py` reads the same version through MCP. Both
use `VERDANT_API_URL` and `VERDANT_ACCESS_TOKEN`; the SDK example additionally accepts
comma-separated `VERDANT_SDK_STORAGE_ORIGINS`. They print only public result identities.
Human OAuth acquisition and service client-credential acquisition stay in Keycloak;
these examples neither collect passwords nor create a second authentication system.

`tests/test_sdk_lifecycle.py` executes actual multipart ingestion, candidate capture,
quality validation, publication and verified downloads with real PostgreSQL/S3 for
both human and service principals. It checks duplicate request keys, paginated members,
REST/MCP identity and revocation, wrong principal kind/audience/scope/issuer and current
audit attribution. JWT signing/JWKS delivery are explicit identity fixtures in that
suite; actual Keycloak browser proof is separate. `tests/test_sdk.py` exercises corrupt
or truncated bytes, disallowed redirects, token/cookie isolation, schema/identity
mismatch, missing contract versions, bounded read retries and unknown command outcomes.
