# Identity implementation and qualification record

Issue #11 is **in progress**. This record distinguishes current implementation and measured proof from remaining acceptance gates. It does not qualify the full roadmap or cluster SSO.

## Current implementation

`src/verdant/auth.py` owns Authlib authorization-code login, S256 PKCE, nonce/state, fixed issuer/audience/RS256 validation, admission and logout. PostgreSQL holds opaque browser sessions and browser-bound, single-use callback attempts. Callback completion is fenced against a concurrent superseding login. Cookies are host-only, HttpOnly and SameSite=Lax; Secure is required unless explicitly enabling the loopback-only qualification configuration. Tokens are not stored in cookies. Cookie-authenticated mutations require the canonical Origin and a session CSRF token. Callback/access log queries are redacted.

External `(issuer, subject)` pairs bind to stable local principals. Email is mutable display data and never an ownership/linking key. Local namespace grants remain authoritative at every shared use-case transaction; stale caller snapshots cannot retain removed permission. The trusted `IdentityService.set_enabled` operator seam increments browser session generation on disable, so re-enable cannot revive old cookies. Provision/grant/disable operations are currently internal Python seams: an operator command and durable identity-administration audit are still pending.

PyJWT verifies access tokens using one issuer JWKS cache, fixed RS256, required issuer/subject/audience/expiry/issued-at/type/client/scope and a three-second network timeout. Key cache has a 300-second TTL and a 30-second unknown-key refresh cooldown; unbounded per-key caching is disabled. REST and mounted FastMCP share this verifier and local service-principal mapping. Browser sessions cannot authenticate the MCP resource. Network key retrieval is offloaded from the async event loop. Live signing-key rotation and outage bounds remain unqualified.

Browser sessions expire at the earlier of the ID token expiry or 30 minutes; callback attempts expire after five minutes. Local principal disable and grant removal are checked on each command. Provider-side group removal is bounded by reauthentication/session expiry, not immediate global logout. Bearer tokens remain valid until expiry unless a local disable/grant change denies the command. Do not describe provider logout as instant token revocation.

## Reproducible local proof

Candidate runtime: Keycloak **26.8.1**, image `quay.io/keycloak/keycloak@sha256:a78d6b79572168cb25886b80a8b5b8d4ed4390743e3eecb810538683e209dd6b`; PostgreSQL **17.6**; Python **3.12**. Authlib **1.9.1**, PyJWT **2.15.1**, starsessions **2.2.1** and FastMCP **4.1.4** are locked in `uv.lock`. React/Vite workspace dependencies are locked in `pnpm-lock.yaml`. New dependencies use OSS licenses; deployment-wide dependency/license qualification remains part of release review.

All fixture accounts, passwords and client secrets under `tests/fixtures/keycloak` are public test-only values. Never use this realm or configuration for organizational access. The fixture uses only loopback HTTP and isolated databases. Existing unrelated Label Studio services were not modified.

Local endpoints: React workspace `http://127.0.0.1:18787`, API `http://127.0.0.1:8788`, Keycloak `http://127.0.0.1:18081/realms/verdant-qualification`. This proves host-process issuer routing; container/cluster canonical issuer routing remains a gate. `scripts/run_identity_fixture.sh` starts the API against the named isolated fixture database. The fixture realm import is `tests/fixtures/keycloak/realm.json`; realm import does not synchronize an existing realm. The test-only `sync_scopes.py` repairs missing fixture scopes without recreating subjects. A complete one-command bootstrap is not yet delivered.

Run the React shell with:

```sh
pnpm --filter @verdant/workspace dev --port 18787
```

`apps/workspace` is the new React/Vite platform shell. `apps/web` remains the earlier digestion UI; it is not this login implementation. Dataset registry interaction belongs to #13 and is not represented as working by the shell.

## Evidence observed on 2026-10-27

- Real in-app browser: owner enters through Keycloak and reaches “Welcome, Olivia Owner.”
- App logout invalidates its server session, follows Keycloak's logout confirmation and returns to “You are signed out.”
- The unadmitted account receives a visible access denial and no local principal is provisioned. “Use another account” forces provider reauthentication; Keycloak's restart-login control permits switching back to the owner.
- The authenticated owner session remains valid after restarting the API with the same database. A [screenshot](../proofs/issue-11/owner-login.png) records the workspace state.
- Real Keycloak client-credentials token: REST creates a dataset; mounted FastMCP reads it with the same internal owner ID. Namespace denial, unauthenticated access and local disable are enforced. `scripts/qualify_identity_service.py` repeats this proof without printing tokens.
- **66 automated tests** pass against isolated PostgreSQL. They cover stable/concurrent subject mapping, stale grants, single-use/expired callback state, callback supersession, session rotation, CSRF, logout, disable/re-enable, REST/MCP parity and protocol errors. Callback-negative tests use the real Authlib cryptographic callback with stubbed issuer HTTP responses; they are not live malicious-Keycloak tests.
- Ruff lint/format, frontend typecheck/production build and Alembic drift checks pass. Live access logs show `/auth/callback` without query secrets.

## Remaining gates

Complete durable identity/operator audit and recovery commands; deployment/runbook documentation; real expiry and keyboard/mobile browser proof; signing-key rotation, unknown-key bursts and issuer outage/recovery; canonical container/cluster issuer routing; repeatable fixture bootstrap; final independent review and exact-head CI. #11 must remain open until its complete acceptance passes. #1 remains open for the full platform roadmap.
