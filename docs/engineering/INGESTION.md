# Verified ingestion transport

The backend implements bounded direct uploads, resumable multipart state, immutable verified sample acceptance, re-upload lineage and staging cleanup. REST and MCP call the same domain services. Native Prefect dispatch now routes upload verification and linked staging cleanup through one fixed ingestion deployment. The existing artifact-verification deployment remains separate. Actual host-process execution and a graceful restart are qualified; packaged runtime and upload/preview UI qualification remain open.

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

## Native execution

The runtime registers `verdant-ingestion/local-ingestion` alongside the existing verification deployment in the same process pool. Deployment parameters contain only namespace ID, operation ID and integer epoch. Workers validate the exact deployment, operation kind, invocation identity and parameters before recording submission, then check current service grants. Known ingestion work remains deferred if its deployment is unconfigured and becomes dispatchable after configuration.

Upload verification uses bounded batches. Cleanup uses a separate `ingest_cleanup` operation with an immutable namespace-owned parent/epoch pointer. Parent artifact and cleanup receipts remain authoritative; the child owns execution admission and can retry pending observations. Cancelling a cleanup invocation does not finish or cancel its parent. This permits cleanup after the parent native run is already final, without reusing that completed invocation.

## Remaining qualification

Packaged Prefect ingestion/cleanup execution, UI controls and file reselection, browser upload/preview/retry/cancel, configured S3 source registration, safe exact-revision preview, process interruptions and retention/fault qualification remain open. Arbitrary HTTP source ingestion is disabled; no URL-fetch endpoint exists. Backend modality storage is byte-oriented; rendering/native modality dependencies will be documented with the actual preview implementation.
