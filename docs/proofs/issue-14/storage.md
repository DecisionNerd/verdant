# Verified ingestion: storage transport checkpoint

Issue #14 remains incomplete. This checkpoint proves the initial concrete transport against the existing isolated private SeaweedFS fixture. It does not prove an authorized ingestion API, artifact ownership, operation integration, UI workflow, cloud compatibility, restart recovery, or total process resource bounds.

## Tested source and backend

The implementation begins from accepted main `be4a945468775ebd879987343481cbcc322159e2` on branch `codex/roadmap-verified-ingestion`. `src/verdant/storage.py` owns S3 transport; namespace authorization, upload admission, artifact placement, retention and sample acceptance belong to domain services and are not supplied by this adapter.

The local fixture is container `verdant-issue12-objects`, endpoint `http://127.0.0.1:18333`, private bucket `verdant-artifacts`, image `chrislusf/seaweedfs@sha256:f7a49e3f3972ebed5be2ed4befa4a0c68eb7bdddba6e692d42729ceb545134b8`. The installed boto3 and botocore versions are both `1.43.119`. The existing fixture's public test-only registry credential was used; no platform secrets or cloud credentials were used. Each test owns a random `qualification/issue14/<uuid>/` prefix and cleans only its own objects and upload IDs. The suite does not create buckets or change CORS policy.

Eleven storage tests passed (nine actual-backend contracts and two configuration/stream-failure tests). Ruff passed. The suite covers direct signed multipart upload and uploaded-part discovery, completion, abort and repeat abort, invalid layout, byte-size and independent SHA-256 verification, missing/corrupt bytes, actual list pagination, ranges, single and bulk delete, concurrent conditional final creation, conflicting final content, signed GET/tampering/expiry, PUT preflight and actual CORS response, and signed Content-Length denial for oversized parts.

The three lost-response cases inject a client exception **after an actual successful provider write**: multipart initiation, completion and final object creation. Exact persisted bytes can then reconcile completion, and conditional final promotion reuses the existing bytes without overwrite. These are deliberate client-side lost-response injections, not process crashes or an interrupted provider connection. Partial multipart resume is exercised through a fresh adapter instance after the first part is stored. No server restart or browser evidence is claimed by these tests.

## Transport contract

The current bounded profile permits up to 256 MiB per artifact, 8 MiB parts, 32 parts, and 120-second signed access. The service must derive each signed part's size from its stored declaration; the transport signs Content-Length, rejects part-number/size overflow, and verifies the complete uploaded layout. Parts are staging only. Browser and worker endpoints are separately configured and signing never rewrites the resulting hostname. Credentials use the SDK's standard credential chain.

Promotion first reconciles any existing final object against the expected digest, without requiring staging to survive. Otherwise it reads staged bytes in one-MiB chunks into a spool that spills to worker scratch above one MiB. It verifies declared size and SHA-256 before conditional final creation, then verifies final bytes independently. The supported artifact cap allows native single-PUT `If-None-Match: *` for final creation; conditional multipart completion is not assumed. Replaying staging upload after promotion cannot change final bytes. Final locations must be allocated by the domain, never supplied by a browser. Signed reads force attachment and application/octet-stream; safe text/image/document preview remains separate work.

Transport errors omit provider response bodies, object keys and credentials. Raw deletion is an internal transport capability; retained-reference authorization and collector/finalizer exclusion must exist before exposing domain cleanup. Existing arbitrary-key sample declarations remain unverified and confer no browser read authority.

## Running the same suite

Set `VERDANT_STORAGE_TEST_ENDPOINT`, `VERDANT_STORAGE_TEST_BUCKET`, and the SDK's ordinary test credential environment, then run:

```sh
uv run pytest tests/test_storage_contract.py -q
```

Use `VERDANT_STORAGE_TEST_LOCAL_HTTP=1` only for the explicit local fixture. Optional `VERDANT_STORAGE_TEST_BROWSER_ENDPOINT`, `AWS_DEFAULT_REGION`, and `VERDANT_STORAGE_TEST_ORIGIN` select the configured browser endpoint, region and pre-provisioned allowed origin. The default origin is `http://127.0.0.1:18787`. This command performs writes/deletes within disposable prefixes and must run only against an explicitly approved test bucket. With no configured target, backend tests skip; skipped tests establish no backend qualification. No cloud destination is currently enabled or qualified by this checkpoint.

A 16,777,217-byte promotion recorded a peak of approximately 3.3 MB of traced Python adapter/SDK allocations, below the 12 MiB test bound. Fixture-byte allocation precedes measurement. This is not process RSS or disk-capacity qualification. Independent read-only review found no remaining blocker in this transport slice after range-stream exception normalization, final-first reconciliation, bounded exact-key initiation discovery and partial-resume tests.

## Remaining acceptance

Add persistent namespace-owned verified artifacts/upload sessions/lineage and atomic ingestion-item outcomes using existing durable operations. Check current grants before signing, source listing, preview and final result acceptance. Reconcile uncertain initiation/completion/promotion, preserve successful items on retry/cancel, enforce cleanup ownership and expiry, and avoid holding PostgreSQL transactions across provider calls.

Constrain source registration to configured S3 ownership/prefixes; reject arbitrary HTTP URLs in the initial profile. Preserve stable sample identity and append content/metadata revisions. Build REST/MCP/UI upload, resume, status, actionable failures and safe exact-revision preview. Prove real browser upload/preview/reload/retry, PostgreSQL concurrent acceptance/revocation, worker crashes, bounded memory/scratch, provider outage/restart persistence, and reference-aware cleanup. Document the actual supported modality/native dependency matrix before closure.

Full regression with workflow dependencies enabled passed all 131 backend tests against new isolated PostgreSQL database `verdant_issue14_test`, with all eleven storage tests using the configured fixture. No database was reset. The first run without workflow dependencies passed 121 tests but skipped the dispatch module; it was superseded by this complete run.

## Atomic ownership and admission checkpoint

The next slice adds namespace-owned artifact declarations, separate storage locations, ingestion items, and explicit sample-to-verified-artifact bindings. Artifact IDs and SHA-256/size are independent of endpoint/location. The domain allocates staging/final keys; callers supply neither. Existing sample declarations are retained and receive no verified binding automatically.

`IngestionService.admit` composes the existing Operations admission body with every item/artifact/location in one caller-owned PostgreSQL transaction. Operation request identity, initial audit/event/outbox and declarations commit together or all roll back. Concurrent duplicate requests reuse one batch. Retries return the original declarations before rechecking subsequently advanced sample revisions. A fresh replacement request validates namespace, stable source identity/modality and expected revision. This admission check does not replace the sample lock and revision recheck required during eventual acceptance.

Requests permit one to 100 unique source identities, strict byte-size limits, explicit replacement target/revision pairs and the existing 64-KiB UTF-8 operation-input bound. Views expose declaration IDs, status, source identity and digests, without raw staging keys, upload IDs or signed URLs. Current namespace grants are checked on admission and reads, including duplicate requests. Upload initiation, signing and final acceptance are not wired yet; the declared operation remains queued without claiming a completed upload.

Migration `0015_ingestion` adds only new tables/guards on this independent main-based branch. Its current predecessor is `0011_workflow_deployment`; reconcile the migration chain with unmerged #15 before integration or platform deployment. No platform migration or rollout occurred. Database triggers freeze artifact identity, verified evidence, locations, item declarations and accepted outcomes. Explicit verified sample bindings must match namespace, sample revision digest/size, verified artifact and placement. A raw-key legacy declaration cannot gain a binding merely because its key matches. Ingestion rows must begin awaiting upload; both direct accepted-row insertion and acceptance without a verified binding are rejected.

All 137 backend tests passed with workflow dependencies enabled against new isolated `verdant_issue14_ownership_test`, including all eleven real/isolated storage tests. Six admission/ownership tests cover competing admission, complete rollback on item failure, conflicting request keys, current read/write/revoked access, cross-namespace denial, replacement retry after sample advancement, database declaration/digest immutability, unverified bindings and contract bounds. After adding the direct forged-insertion assertion, all six focused ingestion tests passed again. Alembic check found no model drift; Ruff and whitespace checks passed. Previous test databases were retained without resets. Independent read-only review found no further blocker in this slice after insertion-guard repair.

Worker acceptance, artifact verification/promotion integration, upload leases/expiry/reconciliation, bounded source registration, REST/MCP/UI and browser/crash/retention qualification remain open. This checkpoint does not complete #14 or roadmap #1.
