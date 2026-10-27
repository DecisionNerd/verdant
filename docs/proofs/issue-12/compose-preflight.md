# Compose preflight

Executed 2026-10-27 on Docker Desktop linux/arm64. This is partial evidence, not issue #12 completion.

Commands are in `infrastructure/compose/README.md`.

- Configuration validation and pinned API/web builds passed.
- Generated credentials were retained byte-for-byte on repeat bootstrap. Parent directory mode 0700; environment file mode 0600.
- First boot created independent `verdant-platform_database` and `verdant-platform_objects` volumes, separate from prior fixtures and legacy data.
- Database initializer created distinct registry and Keycloak owners/databases. Single Alembic job exited 0; API readiness returned schema `0005_identity_audit` through web proxy.
- Database, Keycloak, object-store, API and web health checks passed. Keycloak health waits for its management readiness endpoint before API startup.
- Host and API container independently fetched discovery and returned exactly `http://auth.127.0.0.1.sslip.io:18801/realms/verdant-local`.
- A second `up -d` retained the realm and database and successfully reran the migration job.

Full `down` followed by `up -d` completed successfully, retaining both named volumes. Keycloak logged `Realm 'verdant-local' already exists. Import skipped`; the migration job exited 0 and proxied API readiness again returned schema `0005_identity_audit`. Later sections record durable user/artifact checks. Browser login is not proven by discovery. Registry readiness reports identity as unqualified until explicit qualification evidence; health alone does not certify authentication.

## Generated-credential smoke and restart investigation

`scripts/qualify_compose.py` passed initial schema/discovery/service-token/private-object qualification. The verified service principal was `366bed4a-4e27-49b3-b487-4be8d057c655`; API access without a namespace grant returned 403. Host presigned read passed; anonymous access returned 403. Persistent probe SHA-256: `d10d7e1a8ea1d3fd29f3112b69516982f9820c51301d36a21f1ac2a7c5801b67`.

Restarting PostgreSQL and objects exposed stale Keycloak JDBC connections: discovery remained available, while token issuance returned HTTP 500. API database/schema readiness recovered. A candidate native Quarkus `validate-on-borrow` setting was added; the next paragraph records its successful retest. The `--verify-only` smoke option does not recreate the probe object, so later persistence checks cannot conceal object loss.

The candidate setting passed retest: after Keycloak recreation, `--verify-only` found the original object bytes and metadata. PostgreSQL was then restarted again without restarting Keycloak or API; `--verify-only` passed schema readiness, token issuance, exact same service principal, namespace denial, signed read, and anonymous denial. This proves recovery for the tested single local database restart, not arbitrary outages or concurrent operations.

## Browser and authorized registry proof

The actual in-app browser opened the Compose workspace at `http://127.0.0.1:18800`, followed organizational sign-in to the canonical Keycloak hostname, submitted a generated local qualification account, and returned to `Welcome, Compose Owner.` Screenshot: `compose-login.png`. Human principal: `52ab5089-0255-4f34-add4-ad604a10f8c2`. The account and group were provisioned through the local Keycloak administration API; exact issuer/subject binding used the existing trusted Verdant operator CLI. Credentials remain only in ignored private local configuration.

Sign out invoked the backend, followed Keycloak logout confirmation, and returned to `You are signed out`. Reverse proxy logs recorded paths/statuses without callback query values.

The smoke now creates a synthetic namespace through trusted local operator setup, grants the service write access, creates a dataset, retains its response in a private local receipt, and reads it back with exact owner/response equality. The independent namespace denial remains 403. Creation returned 201 and read returned 200 for dataset `923f09ea-7050-470b-887a-70913ae20359` in namespace `00000000-0000-0000-0000-000000000013`.

After API and database restart, an immediate readiness request returned 502 while Uvicorn restarted. A subsequent smoke passed without restarting the proxy or Keycloak: dataset response/ownership matched the retained receipt, object bytes/metadata matched, service identity was unchanged, and unauthorized/anonymous access remained denied. This startup interval is a real unavailable response, not a successful operation. The smoke now waits up to 45 seconds for registry readiness, retrying only transport errors and gateway/unavailable responses.

## Root entry point and independent review

Root `docker compose config --quiet`, `ps -a`, and `up -d` selected `verdant-platform` and succeeded. The old composition was retained byte-for-byte in root `docker-compose.legacy.yml`, preserving relative mount/include paths. No legacy data was removed.

The source suite passed 73 tests against isolated `verdant_issue12_test`; static checks passed. CI now validates generated Compose configuration and lints bootstrap code.

Independent read-only review found static nginx upstream DNS, socket-only PostgreSQL initialization readiness, and verification that restored grants. Fixes now use Docker DNS runtime upstream resolution, explicit TCP readiness, and retained principal/registry receipts with no operator mutations under `--verify-only`. Initial and read-only smoke both passed after the verifier fix. The final recovery section records successful runtime proxy recreation and clean-volume initialization retests.

## Final recovery retests

- API was removed and recreated while a temporary container occupied its previous address. Address changed from `172.29.0.4` to `172.29.0.7`; unchanged web container recovered through Docker DNS. Read-only smoke passed. Temporary address-holder container was removed.
- Separate project `verdant-issue12-clean` created a new named database volume. TCP readiness released migration after initialization; migration exited 0 and schema query returned `0005_identity_audit`. That project was stopped without deleting its volume.
- Explicitly revoking the qualification service grant caused `--verify-only` to fail with resource HTTP 403. Verification did not restore the grant. The operator then explicitly restored the synthetic grant; read-only smoke passed.
- API-container boto3 generated an internal-host (`objects:8333`) presigned GET using generated scoped credentials; exact persistent SHA-256 matched. Credentials entered through stdin and were not printed. Host signing had already passed with the host endpoint.

Final root `down`, `up -d`, then `--verify-only` passed with the existing dataset, grant, principal and object intact. No operator setup ran in verification mode. Migration exited 0. The final code suite remains 73 passing tests; all 41 Python files passed formatting and lint checks. Workers/Prefect and optional dashboards are absent under the documented registry-only slice allowance; no execution, publication or dashboard behavior is claimed.
