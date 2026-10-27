# Identity operations and recovery

These commands run on a trusted application host with the platform database credential. They are not public HTTP/MCP endpoints and do not grant server access to a browser user. Protect host/database access with the deployment's operator controls. The `--actor` value records an operator assertion; it is not a login credential or a substitute for host authentication. If omitted, audit records explicitly use the trusted-process identity rather than inventing a human actor.

Set `VERDANT_DATABASE_URL` through the existing secret-delivery mechanism. Never paste credentials, bearer tokens or callback URLs into tickets or ordinary logs. Verify the selected environment and back up its database before migrations.

## Runtime configuration

Run the React/Vite workspace and FastAPI behind the same public origin. Keep the browser client secret and database URL server-side. Configure these required environment values before starting `uv run --locked uvicorn verdant.api:create_app --factory`:

| Variable | Meaning |
|---|---|
| `VERDANT_DATABASE_URL` | PostgreSQL service credential and database |
| `VERDANT_OIDC_ISSUER` | Exact canonical Keycloak realm issuer, without a trailing slash |
| `VERDANT_OIDC_CLIENT_ID` / `VERDANT_OIDC_CLIENT_SECRET` | Dedicated confidential authorization-code browser client |
| `VERDANT_APP_URL` | Exact public HTTPS origin, without a trailing slash |
| `VERDANT_OIDC_GROUP` | Exact signed ID-token group admitting humans to the workspace |
| `VERDANT_API_AUDIENCE` | Intended access-token audience for REST/MCP |
| `VERDANT_SERVICE_CLIENTS` | Comma-separated exact client IDs allowed for service access, without spaces |

The browser callback is `VERDANT_APP_URL/auth/callback`; post-logout return is `VERDANT_APP_URL/?signed_out=1`. Require S256 PKCE; disable implicit/password/service-account grants on the browser client. Service clients use client credentials, the API audience and exact `verdant` scope. Include a signed ID-token `groups` claim for browser admission. Never use ID tokens as API tokens.

`VERDANT_INSECURE_LOCAL_HTTP=1` is solely for the explicit local HTTP qualification fixture; omit it for shared deployments. Normal configuration requires HTTPS and Secure cookies. When no issuer is configured, registry HTTP authentication stays fail-closed. Apply migrations before serving traffic; startup does not create/reset schema. `/auth/session` reports the browser session and its CSRF token; cookie-authenticated mutations send `X-CSRF-Token` with the exact Origin. Machine clients call the native REST API or `/mcp/` with an audience-scoped Bearer token, without browser cookies or login redirects.

## Bind and grant access

Resolve the exact canonical issuer and immutable subject from the trusted identity administrator. Email is not a supported lookup/linking key. Service identities must also use an allowed client and the configured API audience/scope; a local mapping alone cannot authenticate a token.

```sh
uv run --locked python -m verdant.operator --actor OPERATOR_UUID provision https://auth.example.org/realms/workspace SUBJECT --kind human
uv run --locked python -m verdant.operator --actor OPERATOR_UUID provision https://auth.example.org/realms/workspace SERVICE_SUBJECT --kind service
uv run --locked python -m verdant.operator --actor OPERATOR_UUID grant PRINCIPAL_UUID NAMESPACE_UUID write
uv run --locked python -m verdant.operator --actor OPERATOR_UUID grant PRINCIPAL_UUID NAMESPACE_UUID read
```

Provisioning prints the stable principal UUID. Repeating it for the same issuer/subject preserves that UUID and ownership. Changing an issuer creates a different identity; do not change issuer configuration as a routing workaround. A human admission group permits application entry but supplies no namespace grant by itself. Disabled principals cannot be silently reactivated through login/provisioning.

Each command commits its state and immutable audit event together. No email or token is copied into the audit payload. Namespace grants remain scoped; global identity events have no artificial namespace. Existing dataset audit retains its namespace requirement. These operations never delete or rebind an identity or transfer dataset ownership.

## Revoke and recover

```sh
uv run --locked python -m verdant.operator --actor OPERATOR_UUID revoke-grant PRINCIPAL_UUID NAMESPACE_UUID
uv run --locked python -m verdant.operator --actor OPERATOR_UUID disable PRINCIPAL_UUID
uv run --locked python -m verdant.operator --actor OPERATOR_UUID enable PRINCIPAL_UUID
uv run --locked python -m verdant.operator audit PRINCIPAL_UUID
uv run --locked python -m verdant.operator cleanup-sessions
```

Grant removal takes effect at the next shared command boundary. Disable rejects browser and service access and advances the browser-session generation. Enable preserves identity, grants and ownership, but old browser cookies remain revoked; the person must sign in again. For service tokens, re-enable can accept a still-valid token: permanently revoke/rotate the service client's credentials at Keycloak when required, and account for already-issued token expiry. These commands do not promise instant provider-token revocation or cancel an already-admitted command.

Session cleanup removes only expired attempts/session rows. Unexpired logout tombstones remain so in-flight saves cannot revive sessions. Audit rows are immutable and are not removed by cleanup. Migration `0005_identity_audit` refuses downgrade while global identity evidence exists; restore a reviewed compatible backup or roll forward instead of deleting audit to force a downgrade.

If an administrator loses namespace access, use these host commands with the intended existing principal UUID; do not create a second account by matching email. Record the incident/operator, exact command target and audit event IDs. Never enable an application password fallback or bypass token issuer/audience checks to recover access.

## Browser and provider recovery

Expired or failed callbacks require a fresh sign-in. Superseded callback attempts cannot establish a session. A denied account can choose “Use another account”; Keycloak may first display its reauthentication view, where “Restart login” opens the username form. App logout clears the local session before attempting organizational logout; Keycloak confirmation is expected when no ID-token hint is retained.

Provider outage does not erase local subjects/grants. Already-cached valid signing keys can continue verifying unexpired service tokens within cache policy; cold/unknown keys fail closed. Do not extend token/session deadlines or disable signature/TLS/issuer checks to mask an outage. Keep public issuer DNS and TLS reachable from browsers and workloads using the same exact issuer.

See [identity qualification](../engineering/IDENTITY-QUALIFICATION.md) for tested versions, current receipts and remaining deployment gates.
