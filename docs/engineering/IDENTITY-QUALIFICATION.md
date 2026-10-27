# Identity implementation and qualification record

Issue #11 implementation has passed local acceptance; final review/CI and merge remain pending. This record distinguishes current implementation and measured proof from remaining acceptance gates. It does not qualify the full roadmap or cluster SSO.

## Current implementation

`src/verdant/auth.py` owns Authlib authorization-code login, S256 PKCE, nonce/state, fixed issuer/audience/RS256 validation, admission and logout. PostgreSQL holds opaque browser sessions and browser-bound, single-use callback attempts. Callback completion is fenced against a concurrent superseding login. Cookies are host-only, HttpOnly and SameSite=Lax; Secure is required unless explicitly enabling the loopback-only qualification configuration. Tokens are not stored in cookies. Cookie-authenticated mutations require the canonical Origin and a session CSRF token. Callback/access log queries are redacted.

External `(issuer, subject)` pairs bind to stable local principals. Email is mutable display data and never an ownership/linking key. Local namespace grants remain authoritative at every shared use-case transaction; stale caller snapshots cannot retain removed permission. The trusted `IdentityService.set_enabled` operator seam increments browser session generation on disable, so re-enable cannot revive old cookies. Registry audit records include verified transport provenance alongside actor identity. Provision/grant/revoke/disable/enable commands use the shared immutable audit stream, with atomic state/audit commits and explicit actor attribution. The [operator runbook](../infra/identity-operations.md) describes the trusted host boundary and recovery commands.

PyJWT verifies access tokens using one issuer JWKS cache, fixed RS256, required issuer/subject/audience/expiry/issued-at/type/client/scope and a three-second network timeout. Key cache has a 300-second TTL and a 30-second unknown-key refresh cooldown; unbounded per-key caching is disabled. REST and mounted FastMCP share this verifier and local service-principal mapping. Browser sessions cannot authenticate the MCP resource. Network key retrieval is offloaded from the async event loop. Live qualification verified warm-key reuse, unknown-key burst suppression, rotated-key acceptance after the cooldown, warm verification through a real provider outage, cold failure and recovery without subject changes.

Browser sessions expire at the earlier of the ID token expiry or 30 minutes; callback attempts expire after five minutes. Local principal disable and grant removal are checked on each command. Provider-side group removal is bounded by reauthentication/session expiry, not immediate global logout. Bearer tokens remain valid until expiry unless a local disable/grant change denies the command. Do not describe provider logout as instant token revocation.

## Reproducible local proof

Candidate runtime: Keycloak **26.8.1**, image `quay.io/keycloak/keycloak@sha256:a78d6b79572168cb25886b80a8b5b8d4ed4390743e3eecb810538683e209dd6b`; PostgreSQL **17.6**; Python **3.12**. Authlib **1.9.1**, PyJWT **2.15.1**, starsessions **2.2.1** and FastMCP **4.1.4** are locked in `uv.lock`. React/Vite workspace dependencies are locked in `pnpm-lock.yaml`. Installed Python dependencies report OSS license metadata, including BSD/MIT/Apache/MPL/LGPL families; no proprietary SSO dependency is introduced. The repository license is unchanged.

All fixture accounts, passwords and client secrets under `tests/fixtures/keycloak` are public test-only values. Never use this realm or configuration for organizational access. The fixture uses only loopback HTTP and isolated databases. Existing unrelated Label Studio services were not modified.

Local endpoints: React workspace `http://127.0.0.1:18787`, API `http://127.0.0.1:8788`, Keycloak `http://127.0.0.1:18081/realms/verdant-qualification`. Host-process, Docker and dedicated Kubernetes Job routing passed using the separate canonical realm described in the [routing proof](../infra/identity-routing-proof.md). `scripts/run_identity_fixture.sh` starts the API against the named isolated fixture database. The fixture realm import is `tests/fixtures/keycloak/realm.json`; realm import does not synchronize an existing realm. The test-only `sync_scopes.py` repairs missing fixture scopes without recreating subjects. Full-platform one-command bootstrap is owned by #12. These are isolated identity qualification fixtures.

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
- Browser namespace proof: the owner reads its own dataset through the cookie-authenticated API; an admitted account with no namespace grant receives “Namespace access denied” for that same URI. [Owner receipt](../proofs/issue-11/owner-resource.png) and [denial receipt](../proofs/issue-11/denied-resource.png) record the results.
- The shell renders at a real 375px iframe viewport with readable text and visible controls; [narrow-viewport proof](../proofs/issue-11/mobile-375.png) is a desktop-browser responsive check, not mobile-device certification.
- Real Keycloak client-credentials token: REST creates a dataset; mounted FastMCP reads it with the same internal owner ID. Namespace denial, unauthenticated access and local disable are enforced. `scripts/qualify_identity_service.py` repeats this proof without printing tokens.
- **72 automated tests** pass against isolated PostgreSQL. They cover stable/concurrent subject mapping, stale grants, single-use/expired callback state, callback supersession, session rotation, CSRF, logout, disable/re-enable, REST/MCP parity and protocol errors. Callback-negative tests use the real Authlib cryptographic callback with stubbed issuer HTTP responses; they are not live malicious-Keycloak tests.
- Real session expiry displays “Your session has expired.” Keyboard Tab reaches sign-in; Return reauthenticates through the still-valid Keycloak SSO session without another password. The [expiry screenshot](../proofs/issue-11/session-expired.png) records the expired state.
- `scripts/qualify_key_rotation.py` observed one JWKS fetch for twenty warm checks and thirty unknown-key requests. After 31 seconds, the rotated signing key verified. Warm cached verification survived a real issuer stop, cold verification failed closed, and restart preserved user subjects. The temporary key was removed after qualification.
- Ruff lint/format, frontend typecheck/production build and Alembic drift checks pass. Live access logs show `/auth/callback` without query secrets.

Shared OTel instrumentation now covers registry, identity and async authentication boundaries, with bounded operation/outcome labels and a separate authentication-failure counter. Optional telemetry failure neither bypasses admission nor blocks committed state. Live Collector delivery is deployment qualification, not a claim made by the unit exporter tests.

## Remaining gates

Complete final independent review, exact-head CI, merge and issue-state verification. Production TLS/ingress and the complete platform bootstrap remain separate deployment acceptance in #12. #11 must remain open until its complete acceptance passes. #1 remains open for the full platform roadmap.
