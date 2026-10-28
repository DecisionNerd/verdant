# Local platform bootstrap

This composition starts the implemented registry, workspace, and identity services. Root Compose selects this stack; legacy composition is isolated in `docker-compose.legacy.yml`. Do not mix the two compositions or their data volumes.

From the repository root:

```sh
python3 infrastructure/compose/bootstrap.py
docker compose --env-file infrastructure/compose/.local/compose.env -f infrastructure/compose/compose.yaml config --quiet
docker compose --env-file infrastructure/compose/.local/compose.env -f infrastructure/compose/compose.yaml up -d --build
docker compose --env-file infrastructure/compose/.local/compose.env -f infrastructure/compose/compose.yaml ps -a
```

Workspace: http://127.0.0.1:18800. Keycloak: http://auth.127.0.0.1.sslip.io:18801. Objects: http://127.0.0.1:18833. API/MCP share the workspace origin. Only these loopback endpoints are published. The canonical issuer uses the same hostname and port in the browser and Docker network. Local HTTP and Keycloak development mode are local-only settings.

The bootstrap creates random credentials in ignored `.local/`, with a private parent directory. Repeat invocation preserves every file. Preserve this directory with the named volumes; deleting it and generating new passwords will not update existing databases or imported realms. The realm starts without human accounts. Create users and workspace group membership through Keycloak, then provision issuer/subject identities and grants with the existing Verdant operator CLI. The generated bootstrap admin password is in `.local/compose.env`; do not paste it into issue evidence.

The isolated Compose project is `verdant-platform`. PostgreSQL initializes distinct registry and Keycloak owners/databases on an empty volume only. Alembic runs as one migration job before API startup. Realm import skips an existing realm. Whole object payload and filer metadata persist in one `/data` volume. Registry credentials are scoped to `verdant-artifacts`; bootstrap administration uses a separate credential.

Stop while retaining data:

```sh
docker compose --env-file infrastructure/compose/.local/compose.env -f infrastructure/compose/compose.yaml down
```

Do not add `--volumes` during routine shutdown or recovery. No reset is performed by bootstrap.

Recorded qualification covers browser login/logout, service authorization, host/container signing, clean database initialization, and dependency restart recovery. Review and final lifecycle qualification are tracked in the issue #12 evidence. Coordinated platform backup is separate from the qualified quiescent object-store restore. The base composition remains registry-only. Native execution is available through the optional workflow overlay below; its qualification evidence is tracked separately.

Run the bounded local smoke after startup:

```sh
uv run --locked python scripts/qualify_compose.py
uv run --locked python scripts/qualify_compose.py --verify-only
```

The first command provisions the configured service identity through the trusted operator CLI and creates one synthetic probe object conditionally. Repeated runs preserve its bytes. Use `--verify-only` after restart; it cannot recreate a lost object. The smoke grants its service identity write access only to a synthetic qualification namespace. A separate namespace remains inaccessible. The probe creates and reads a synthetic dataset, checks a valid service token is denied access without a grant, and checks anonymous versus presigned storage access. It does not replace browser or lifecycle acceptance.

Keycloak uses native Quarkus JDBC validation on borrow through `quarkus.properties`. This adds a database validation round trip when borrowing a connection and prevents stale pooled connections from breaking token issuance after PostgreSQL restarts. Retest this runtime property on Keycloak upgrades.

## Optional workflow execution

The native Prefect execution overlay uses a separate persistent PostgreSQL database and generated machine credentials. It publishes no additional host ports and disables the Prefect dashboard. The worker executes the fixed artifact-verification and ingestion flows; PostgreSQL registry Operations remains the logical authority. Ingestion dispatch handles uploads and separate linked cleanup operations.

```sh
python3 infrastructure/compose/bootstrap-workflows.py
docker compose --env-file infrastructure/compose/.local/compose.env --env-file infrastructure/compose/.local/workflows.env -f infrastructure/compose/compose.yaml -f infrastructure/compose/workflows.yaml config --quiet
docker compose --env-file infrastructure/compose/.local/compose.env --env-file infrastructure/compose/.local/workflows.env -f infrastructure/compose/compose.yaml -f infrastructure/compose/workflows.yaml up -d --build
```

Preserve `.local/workflows.env` with the `workflow-database` volume. Repeated bootstrap retains credentials; it does not change existing platform inputs or reset any database. Native Prefect starts its own metadata migrations before becoming healthy. One registration job creates/updates the fixed process pool and both deployments before worker and dispatcher startup. One workflow image build supplies all execution services.

The service identity still requires explicit operator provisioning and namespace write grants. The dispatcher discovers only currently authorized namespaces, at most 100 per discovery page, and cycles pages. Each namespace delivery batch is bounded. New grants become eligible without restarting the dispatcher; revoked grants are rechecked before claims and commands. The worker independently verifies Keycloak service identity and current grants for execution.

Stop the overlay using the same two `-f` files and private env files with `down`, without `--volumes`. Keep private inputs and both PostgreSQL volumes together for recovery. Native process workers inherit service credentials from container environment; deployment parameters contain only namespace ID, operation ID, and epoch. No cloud account or model access is configured.

Packaged-runtime qualification is tracked in `docs/proofs/issue-19/verification.md`; configuration validity and container health alone do not prove operation execution or browser recovery.

### Optional read-only ingestion sources

Set `VERDANT_S3_SOURCE_PREFIX` consistently for API and workflow services to enable existing-object registration in the primary bucket. The default empty value disables this capability. Object references resolve below `<prefix>/<namespace UUID>/`; users supply relative keys, expected SHA-256 and size. Keep source provision/write authority separate from application registration. Workers copy verified bytes into their owned final artifacts and never clean up source originals. The UI keeps source registration distinct from file upload. Enabling configuration alone is not backend or browser qualification; see `docs/proofs/issue-14/storage.md`.
