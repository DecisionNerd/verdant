# Local object-store qualification

Issue #12 remains in progress. This receipt covers the object service and Python image prototype; it does not establish that the target Compose bootstrap starts the platform. Root Compose still points at the legacy digestion stack at this checkpoint.

## Candidate and boundary

Observed on 2026-10-27: SeaweedFS OSS **4.51**, source `153ab668c99731a6d29492bf6c61d0b4fa701101`, Apache-2.0. Image index:

```text
chrislusf/seaweedfs@sha256:f7a49e3f3972ebed5be2ed4befa4a0c68eb7bdddba6e692d42729ceb545134b8
```

Native runtime was Linux/arm64; the arm64 manifest is `sha256:7e68a388de0806ed8d96f30cb7e927182a3d80d42920cb87d97d23311a7a1a77`. Registry availability of other architectures is not runtime qualification. Boto3/botocore **1.43.119** are locked in `uv.lock`. SDK checksum behavior was retained; ETag is not treated as an application SHA-256.

The owned `verdant-issue12-objects` fixture publishes only `127.0.0.1:18333:8333`. Its named volume `verdant-issue12-objects-data` mounts the entire `/data`, including filer metadata at `/data/filerldb2`. Static test identities are mounted read-only from a separate configuration file. No AWS credential environment variables grant a broad server Admin identity. Only S3 is published; WebDAV and browser admin UI are disabled.

Observed command flags:

```text
mini -dir=/data -ip=objects -ip.bind=0.0.0.0 -s3.port=8333
-s3.config=/run/secrets/objects-s3.json -s3.autoCreateBucket=false
-bucket=verdant-artifacts -s3.allowedOrigins=http://127.0.0.1:18787
-webdav=false -admin.ui=false -s3.port.iceberg=0 -s3.port.lance=0
-master.telemetry=false -master.volumeSizeLimitMB=64
-master.volumePreallocate=false -volume.max=16
```

The `mini` image entrypoint supplies `weed`; do not prepend another `weed` command. The small volume limits are qualification settings, not production capacity guidance. Credentials in `tests/fixtures/storage/identities.json` and the scripts are public test-only values. Deployment bootstrap must generate private credentials outside tracked source.

## Verified capabilities

`scripts/qualify_object_store.py` passed from the host at `http://127.0.0.1:18333` and a non-root source-built Python container at `http://objects:8333`:

- Separate private owner buckets; each scoped owner can read/write its bucket, while anonymous listing/Get/Head/Put and cross-owner reads/listing are denied.
- Put/Get/Head, byte length, user digest metadata, range reads and byte equality.
- Concurrent create-if-absent yields one success and one 412; the successful content remains intact.
- Pagination with three objects over two pages, without duplicate/skipped keys.
- Multipart upload/complete and abort, preserving the resulting bytes.
- Signed PUT/GET, expired URL denial, bucket-path tampering denial and signed Host tampering denial.
- Exact-origin CORS preflight plus an actual browser PUT/GET whose SHA-256 matched. The [browser proof](../proofs/issue-12/browser-s3.png) contains only synthetic test data; signed URLs are kept in an ignored short-lived `proof.json`, not screenshots or logs.
- Restart with the named volume preserves bytes, SHA-256 metadata, private access and the exact bucket CORS configuration.

The first immediate post-restart SDK request raced startup. Qualification now waits on bounded HTTP and signed SDK readiness, without resetting data or treating authentication failures as transient startup success. Compose health dependencies must provide the same meaningful sequencing.

## Backup and restore

`scripts/qualify_object_backup.py` stops only the named owned fixture, archives all `/data`, extracts it into a separate new volume, and starts a temporary restored service with the same external credential configuration. It verifies the retained object digest, digest metadata, exact CORS and anonymous denial, then restores the original service and verifies it again.

Observed volumes were `verdant-issue12-backup-e2698cd5` and `verdant-issue12-restore-e2698cd5`; the temporary restored container was removed. The original object service recovered. External credential/configuration files must be preserved separately; the archive is not a backup of secrets or a coordinated platform PostgreSQL/object snapshot. This is a quiescent single-node development restore proof, not an HA or live-backup claim.

## Python image prototype

`infrastructure/compose/Dockerfile.api` builds the locked Python runtime, includes migrations/license, and runs Uvicorn directly as PID 1 under user `10001:10001`. Image ID `sha256:5796e9919b4c1d9b68d0893ddfc060178c7b70a6f765247fa5b517773bf0bd0d` started through its default command, reached schema `0005_identity_audit`, rejected unsigned identity and shut down on SIGTERM with exit 0. The storage contract also passed inside this image. This is not yet the complete API/web/worker composition or end-to-end configured container login.

## Repeat the storage checks

With the named fixture running and public test-only identities mounted:

```sh
uv run --locked python scripts/qualify_object_store.py
# Restart the owned service, retaining its volume and configuration.
uv run --locked python scripts/qualify_object_store.py --verify-restart
uv run --locked python scripts/qualify_object_backup.py
```

For the browser fixture, serve `tests/fixtures/storage/browser.html` through the local Vite development server, then prepare fresh scoped URLs for that exact origin:

```sh
uv run --locked python scripts/qualify_object_store.py \
  --origin http://127.0.0.1:18790 \
  --browser-config tests/fixtures/storage/proof.json
```

URLs expire after 120 seconds. Run the page's upload/download button before expiry. Never put real storage credentials into that JSON or frontend assets. Preserve existing data volumes; these scripts never perform an implicit reset.

## Remaining #12 work

Implement generated private configuration and bucket/realm/database bootstrap; core Compose/migration/health sequencing; web image and real workflow-profile commands; complete browser/container identity wiring; down/up and dependency recovery; legacy composition isolation; CI and independent readiness review. Domain ingestion, immutable publication, retained-reference garbage collection, and integration-specific MLflow/Mimir/Loki/Tempo clients remain their own acceptance slices. No unsupported capability is inferred from these object-service checks.
