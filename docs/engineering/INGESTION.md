# Verified ingestion transport

The backend implements bounded direct uploads, resumable multipart state, immutable verified sample acceptance, re-upload lineage, configured read-only source registration and staging cleanup. REST and MCP call the same domain services. Native Prefect dispatch now routes upload verification and linked staging cleanup through one fixed ingestion deployment. The existing artifact-verification deployment remains separate. Actual host-process execution and a graceful restart are qualified; packaged runtime and browser qualification remain open. The exact-revision inspection UI exposes verified text/image previews and attachment downloads.

## Configuration and identity

Set `VERDANT_S3_ENDPOINT` and `VERDANT_S3_BUCKET` for the internal SDK. `VERDANT_S3_BROWSER_ENDPOINT` selects the actual browser-reachable signing origin; it defaults to the configured endpoint. URLs are signed for that hostname directly. `VERDANT_S3_REGION` defaults to `us-east-1`; `VERDANT_S3_ADDRESSING_STYLE` accepts `path` or `virtual`. HTTP requires the explicit local-development flag `VERDANT_INSECURE_LOCAL_HTTP=1`.

The SDK uses its normal credential chain. A deployment may instead supply both `VERDANT_S3_ACCESS_KEY` and `VERDANT_S3_SECRET_KEY`. Supplying only one fails configuration validation. Credentials stay on the backend. Compose configures the API's internal address as `http://objects:8333` and browser signing address as `http://127.0.0.1:18833`, using the existing scoped registry secret. Configured storage describes deployment inputs; reachability and backend qualification require separate evidence.

The browser uses its existing opaque server session, same-origin writes and CSRF header. Service clients use verified service JWTs with the configured audience/client/scope and issuer/subject binding. Namespace grants are checked for each command. Read grants permit history and limits; upload changes require write grants. An absent storage configuration rejects new upload admission while authorized history and cancellation remain available.

## Upload protocol

Each artifact is bounded to 256 MiB, with eight-MiB parts and at most 32 parts. One admission accepts at most 100 unique source identities within the shared 64-KiB UTF-8 command bound. File bytes are excluded from admission payloads. Server-generated object locations, credentials and signed URLs are excluded from logical admission identity. The browser transfers bytes directly to object storage; the API receives declarations and multipart control metadata.

1. Read namespace upload limits. Admit source identity, modality, SHA-256, size, metadata and optional explicit replacement target/revision. Reuse the same request key and inputs after a lost admission response.
2. Initiate each item. The server allocates staging and native upload identities, and records uncertainty before provider I/O. Reloading resolves committed progress or reconciles one exact existing upload.
3. Request each part URL using epoch and part number. Its signed Content-Length derives from the stored declaration. URLs last 120 seconds. Discover uploaded parts after reconnecting and send only missing parts.
4. Complete each item. Invalid/incomplete parts remain repairable. Uncertain completion can repair only the exact still-active native upload. Existing or missing provider facts are handled explicitly.
5. Ready items await worker verification. The last ready item queues the operation. Workers independently verify SHA-256/size, promote to a final key inaccessible to upload URLs, verify final bytes and atomically accept registry revisions.

`ready` means multipart bytes exist and have the declared size. Verified artifact ownership and usable sample revisions appear only after worker acceptance. Signed URLs remain scoped bearer access until expiry; revocation blocks renewed signing and later acceptance. Parent/epoch/state and current authorization are rechecked after provider observations before disclosure or commit.

## REST and MCP

All REST paths below are relative to `/api/v1/namespaces/{namespace_id}`. Browser responses and signed capabilities use `Cache-Control: no-store`. JSON command bodies carry bounded typed inputs; HTTP errors distinguish denied access, revision/state conflicts and storage unavailability.

| REST | MCP tool | Purpose |
|---|---|---|
| `GET /ingestions/limits` | `ingestion_limits` | Configuration and fixed upload bounds |
| `GET /ingestions?after=&limit=` | `ingestions_list` | Authorized bounded operation discovery |
| `POST /ingestions/uploads` | `ingestion_upload_admit` | Admit a bounded declaration batch |
| `GET /ingestions/{operation_id}` | `ingestion_get` | Reload state and recovery links |
| `POST /ingestions/{operation_id}/items/{item_id}/initiate` | `ingestion_upload_initiate` | Initiate/reconcile native upload |
| `POST /ingestions/{operation_id}/items/{item_id}/sign-part` | `ingestion_upload_sign_part` | Sign declared part number/size |
| `GET /ingestions/{operation_id}/items/{item_id}/parts?epoch=` | `ingestion_upload_parts` | Discover uploaded parts |
| `POST /ingestions/{operation_id}/items/{item_id}/complete` | `ingestion_upload_complete` | Complete/reconcile and mark ready |
| `POST /ingestions/{operation_id}/cancel` | `ingestion_cancel` | Request logical cancellation |
| `POST /ingestions/reuploads` | `ingestion_reupload` | Recover pinned failed items from their IDs |

Initiate, complete and cancel use `{"epoch": 1}`. Signing additionally requires `part_number`. The API/OpenAPI and MCP schemas define complete declaration shapes and validation. Worker acceptance, arbitrary storage deletion and caller-asserted verification are not public commands.

## Recovery after reload

A transient provider outage can retry the existing logical operation through the existing Operations control, preserving accepted items and stable effects. Corrupt bytes require a fresh upload identity. Submit a new request key and the original item IDs to `/ingestions/reuploads`; the server derives original metadata and replacement pins. Browser-retained declaration JSON is unnecessary.

Re-upload requires failed/cancelled origins and exact pinned inputs. Changed input requires an explicit new registration decision. One transaction creates the linked operation and fresh staging, supersedes each original item and preserves accepted siblings. Original failure history remains visible. Item history returns both the immediate retry origin and replacement operation/item links, allowing recovery after lost responses or reloads. Superseded batches cannot reactivate their old items.

Cancellation remains requested while provider writes or native admission are uncertain. Cleanup removes only exact owned staging and retains final artifacts. A bounded position cursor lets later items progress past unresolved facts. A pending cleanup requires further observation or qualified reconciliation, not blind write replay. See the [storage proof](../proofs/issue-14/storage.md) for actual and fixture-only evidence.

## Browser upload and recovery

Choose **Ingest data** in a workspace. Select up to the displayed file count/size limits, review each source identity and modality, then create the upload operation. File names initialize source identities but never choose object locations. SHA-256 is computed one file at a time with native Web Crypto; this requires a secure browser context. Hashing materializes at most one declared file, up to 256 MiB, in browser memory; browser/process peak memory is not yet qualified. The API still receives only bounded declarations and multipart control metadata.

Select **Upload or resume files** to transfer. The browser checks the selected files' complete digest and size against every pending declaration before issuing provider writes. It discovers existing native parts, skips matching part sizes, signs missing parts and sends them directly to object storage without application cookies. Empty artifacts use one zero-byte part. A missing multipart upload after uncertain completion invokes the existing completion reconciler; it does not recreate the upload. Completed parts await independent worker verification before the UI calls a sample accepted.

The upload history is paginated and refreshes automatically. A selected operation has a reloadable `view=ingestions&ingestion=...` link. After reload, reselect original files; digest and size identify their content even if local file names change. Bytes, file handles, declaration bodies and signed URLs are not persisted in browser storage. An uncertain admission retry preserves its exact command and request key while the component remains mounted. Definitive input conflicts/validation failures unlock editing. After reload, server history discovers committed admissions whose response was lost.

**Stop local transfer** aborts local transfer and prevents subsequent parts/completion from that invocation; it does not claim logical cancellation. **Cancel upload operation** fences subsequent server commands and admits linked staging cleanup. It is also available for failed parents. Uncertain provider effects can keep cancellation pending; the cleanup link opens the durable operation. Accepted final artifacts remain preserved.

A failed/cancelled operation can create a linked re-upload of every unfinished, non-superseded item, including items still marked ready whose independent verification failed. Original metadata and target pins derive from server-side origins. Accepted siblings remain untouched. Verification retry is offered only when all items are ready or accepted. A pre-verification size failure in a running upload requires cancellation/cleanup before re-upload. Connection failures show last known status and pause writes; denied access hides details.

Accepted items offer exact content inspection and **Replace bytes**. Replacement preserves source identity, modality and selected revision metadata, and pins that sample revision as the expected predecessor. A concurrent newer revision produces a conflict; the UI does not silently overwrite it. Uncertain replacement admission retries preserve the same request identity. The replacement then follows the same upload/verification path and appends a revision under the stable sample identity.

## Register configured S3 sources

`VERDANT_S3_SOURCE_PREFIX` explicitly enables source registration in the configured primary bucket. It defaults to disabled. API and worker must use the same value. Relative object `reports/page.txt` resolves to `<configured-prefix>/<namespace-uuid>/reports/page.txt`; namespace is always supplied by the authorized server command, never selected inside a URL. Prefix and relative keys are canonical paths: no empty/dot/traversal segments, absolute paths, URL schemes, backslashes, percent escapes, query strings, fragments or control characters. The complete key is limited to 1024 UTF-8 bytes. No source credentials or alternate endpoints enter commands.

In the **Ingest data** view, add source rows with relative object key, stable source identity, modality, expected SHA-256 and size. Up to 100 items fit the shared bounded command. Source objects must already exist in the configured workspace source area. The server queues a separate `ingest_source` operation without multipart upload or source mutation. Acknowledged registration clears the form for the next batch; uncertain retries preserve the exact command identity. Histories expose read-only relative references and ordinary durable state. A source operation never exposes file-upload controls.

REST `POST /ingestions/sources` and MCP `ingestion_source_admit` accept the same request-key/items structure as upload admission, with `source_object` on each item. The source scope (store ID, bucket, prefix), declarations, metadata and replacement pins are captured in immutable operation inputs. The worker revalidates this exact scope against current configuration, streams and verifies the source into bounded scratch, conditionally writes an owned final artifact, verifies final bytes, then uses the same atomic sample/revision acceptance transaction. A changed scope rejects acceptance; endpoints and signed URLs remain outside artifact identity.

Source failure records missing/corrupt/unavailable outcomes without accepting a sample. Verification retry retains the original pins. Explicit new declarations are required for changed bytes; a source operation may also recover through a linked fresh **file upload**, preserving its original declaration and origin lineage. API source declarations support explicit sample/expected-revision targets. Existing accepted content can use the UI's pinned file replacement path.

Source originals are read-only throughout admission, verification, retry, cancellation and cleanup. The source reference is never a staging-deletion target. Unknown promotion reconciles only against verified owned final bytes; linked maintenance removes only the operation's separately allocated staging key. Disabling source registration does not prevent authorized cleanup of those owned effects. Migration 18 extends immutable cleanup/re-upload guards to source parents and refuses downgrade while any source history exists.

## Exact-revision content inspection

`GET /samples/{sample_id}/revisions/{revision_id}/preview` and MCP `sample_revision_preview` resolve the same verified artifact binding. A legacy caller-declared object key never authorizes reads. The namespace, sample, revision, artifact digest/size and configured store/bucket must match. Every byte is independently verified into bounded scratch before returning content or a signed attachment link. Current grants and placement are checked again after provider I/O and signing. The response is no-store; the browser keeps it only in the mounted inspection component.

Text/document/transcription content supports a valid UTF-8 excerpt of at most 64 KiB, rendered as escaped plain text. Truncation is explicit and does not split a UTF-8 code point. PNG/JPEG sources up to eight MiB and 20 million pixels become metadata-free PNG thumbnails with a maximum edge of 1024 pixels. Pillow is an OSS runtime dependency for bounded image decoding and reencoding. WebP stays download-only: its native animation decoder may allocate before Pillow exposes dimensions. PDF, other documents, unsupported binary content and larger images use attachment downloads rather than inline execution. No OCR, PDF renderer, markup interpreter, audio or video decoder runs in this preview service.

The browser requests the exact selected revision on demand. Refresh clears previous content before a new authorization result; inspection changes discard late responses. Signed original-download links force `application/octet-stream` and attachment disposition, expire after 120 seconds and disappear from the component at expiry. A link remains a bearer capability until provider expiry; grant revocation prevents newly requested capabilities and response disclosure, not previously received bytes. The UI shows preview/read failures without claiming unverified content is usable.

## Native execution

The runtime registers `verdant-ingestion/local-ingestion` alongside the existing verification deployment in the same process pool. Deployment parameters contain only namespace ID, operation ID and integer epoch. Workers validate the exact deployment, operation kind, invocation identity and parameters before recording submission, then check current service grants. Known ingestion work remains deferred if its deployment is unconfigured and becomes dispatchable after configuration.

Upload verification uses bounded batches. Cleanup uses a separate `ingest_cleanup` operation with an immutable namespace-owned parent/epoch pointer. Parent artifact and cleanup receipts remain authoritative; the child owns execution admission and can retry pending observations. Cancelling a cleanup invocation does not finish or cancel its parent. This permits cleanup after the parent native run is already final, without reusing that completed invocation.

## Combined schema and rollout boundary

The released target migration chain is `0011_workflow_deployment` → candidate capture/generations/interpretation (`0012`–`0014`) → ingestion ownership/recovery/execution/sources (`0015`–`0018`). Migration 15 follows 14. Fresh installation and an actual schema-14 writer fixture upgraded to 18 are qualified separately; the latter preserves every preexisting row hash and resumes partial candidate preparation with its original interpretation pins.

Early standalone #14 test databases were created while migration 15 still followed 11. They are historical proof stores: a revision label of 18 does not make them combined-schema databases. Preserve them and use fresh combined proof databases or the verified 14→18 upgrade path. Do not stamp missing candidate migrations as applied. The native qualifier now accepts only its explicit integrated test database.

Roll out matching images with platform writers coordinated: stop API, dispatcher and process workers before migration, retain a coordinated registry/object-store/workflow checkpoint and private inputs, apply the migration job once, register deployments, then start matching API/worker/dispatcher images. Old schema-14 writers must not overlap new ingestion acceptance: an old metadata writer does not know verified artifact bindings. After new ingestion history exists, prefer a forward repair; restoring an old application image alone is not a preserving rollback. Restore a qualified coordinated checkpoint when an older schema is required. Automatic downgrade/reset is not part of this rollout.

The actual packaged runtime is now upgraded to18 after a coordinated private checkpoint. All22 old registry table row hashes/counts and the existing partial browser candidate were preserved before writers resumed. Actual Keycloak sign-in/reauthentication, browser text/image upload, configured source registration, native acceptance/cleanup and exact previews are qualified. An isolated coordinated checkpoint restore is qualified below; adverse recovery/resource gates remain separate. See the storage proof for exact image/run receipts and scope.

## Remaining qualification

Actual browser cancellation before and after partial transfer, linked re-upload after reload, wrong-file rejection, pinned replacement/stale conflict and eight-part resume are now qualified. Denied-user browser paths, process/provider faults and memory/retention bounds remain open. Arbitrary HTTP source ingestion is disabled; no URL-fetch endpoint exists. Backend modality storage remains byte-oriented. Inline rendering supports the bounded text/PNG/JPEG paths above; other modalities retain verified original downloads.


### Coordinated checkpoint restore qualification

The schema-14 checkpoint was restored into an internal Docker network with no host ports. Native PostgreSQL archives retained original owners and privileges for registry, Keycloak and Prefect; an additional isolated bootstrap role performed restoration. Original SeaweedFS data and service configuration were restored. The original backup omitted the tracked Keycloak `quarkus.properties` bind mount. Qualification supplemented it from verified pinned Git configuration; the original checkpoint was not self-contained.

Before migration, all 22 registry table counts and row hashes matched. Actual restored Keycloak client credentials and JWKS resolved the existing registry service identity and grant. Three objects from succeeded verification operations passed digest checks. Six restored Prefect run references matched deployment, idempotency key and parameters, including their original failed/crashed states. Clone-only migration to schema 18 preserved all old row hashes. Its preparing candidate resumed from 100 to 101 sealed members with the complete original membership and interpretation pin digest unchanged. The live candidate remained preparing100 at source/latest revision102.

`scripts/qualify_checkpoint_restore.py` requires a unique database marker before inspection or candidate mutation. Run it only against an isolated restored environment, before migration and then with `--upgraded`. Retain private credentials and diagnostic output outside Git. The [safe receipt](../proofs/issue-14/checkpoint-restore.json) records bounded evidence. Clone containers stopped after proof; their volumes and checkpoint remain preserved. This does not qualify restored browser UI or replace the independent live-browser gate.

Future checkpoints must inventory every bind mount and retain its bytes or an exact verified Git source, plus coordinated databases, objects, roles, private inputs and image pins. Stop writers before capture; restore into isolation and verify identities, grants, objects and workflow references before any recovery cutover.


Runner process recovery has two real SIGKILL regression cases in `tests/test_ingestion_process.py`: before promotion and after final creation but before acceptance. Both preserve the durable unknown receipt and recover exactly once, including final-only recovery after owned staging removal. These controlled runner boundaries do not establish native Prefect worker supervision or arbitrary transfer-fault recovery.
