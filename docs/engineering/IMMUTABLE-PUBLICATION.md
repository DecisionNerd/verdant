# Immutable dataset publication

Publication turns one sealed candidate and its exact passing quality report into
an immutable dataset version. PostgreSQL owns its identity, checkpoints, retention
and committed result. The working draft remains editable. This is the implemented
#17 slice on the development dependency stack; qualification and delivery limits
are recorded in [the proof](../proofs/issue-17/publication.md).

## Prepare, inspect and publish

In the dataset workspace, select a sealed candidate and a completed passing
quality report. Choose a unique version label and a selection mode, then start
publication preparation. Labels contain 1–100 characters: a letter or number
first, followed by letters, numbers, dots, underscores or hyphens.

- **Selected candidate** publishes the captured generation even if the draft changes.
- **Current draft must still match** publishes only while the draft revision still
  equals the candidate's captured revision. A conflict retains the preparation;
  it never switches candidates or silently changes modes.

The candidate, report, label and mode are fixed at admission. Resume source checks
in batches of up to 25 members. Each frozen revision must have an accepted artifact
binding in the configured store. The trusted runner protects the artifacts before
reading their actual bytes, verifies exact size and SHA-256, then checkpoints the
verified batch. Shared artifacts need one byte read per batch, while progress
counts members. The final preparation call also builds and verifies the manifest.

Inspect the captured/latest draft revisions, pinned report and policy revision,
checked count, manifest digest/size and protection deadline. A `prepared` group
means source and manifest verification completed; it does not mean a version was
published. Click **Publish immutable version** to commit. The UI warns when a
known current-draft mismatch will conflict. Read-only users can inspect publication
evidence; current authorization failures hide cached evidence and controls.

## State, retries and failure recovery

The states are `building → prepared → published`, or
`building/prepared → releasing → released`. Published groups and versions are
immutable. One group has one logical `publish_dataset_version` operation and at
most one version. Version labels are unique within a dataset.

Admission uses the existing operation request identity. Reuse the same request
key and identical input after a lost response; a conflicting reuse is rejected.
The UI preserves and locks the original admission input until that retry resolves.
Preparation sends `expected_count`: an older checkpoint returns committed progress,
while a checkpoint ahead of committed progress conflicts. Fresh claims, renewal
and release advance the group epoch, fencing delayed verification attempts.

| Observation | Recovery |
| --- | --- |
| Source missing, corrupt or unavailable | The group stays unready; committed progress and protection remain. Fix the source problem and resume, or release the abandoned preparation. |
| Manifest upload response lost | Retry against the same allocated artifact/key/digest. Conditional creation and independent byte readback reconcile the result. No success is accepted from upload acknowledgement alone. |
| Manifest exceeds 256 MiB | `manifest_too_large` retains progress and protection. Capture a smaller candidate; the supported cap is not raised automatically. |
| Worker scratch unavailable | `preparation_io_unavailable` retains progress and protection. Restore scratch capacity before retrying. |
| Finalization response lost | Read the group or retry finalization on that same group. A committed version returns its original identity; no second version is created. |
| Draft changed, label already exists, or dataset archived | Finalization conflicts and retains preparation. Its fixed target and mode remain unchanged. |
| Current grant revoked or principal disabled | Reads/writes fail current authorization. Restore authorized access through the existing identity administration path. |
| Protection expired | Renew an unreleased group to restart checking every member under a fresh epoch and 24-hour deadline, or explicitly release it. |

The default preparation deadline is 24 hours. Expiry blocks publication but does
not by itself remove retention: `building`, `prepared` and `published` references
protect artifacts even after the deadline. Explicit release, or the managed
`PublicationService.expire` path after checking the deadline, serializes on the
group and removes at most 100 references per command. Continue until `released`.
Published protection cannot be released. No automatic expiry scheduler is added.

## Manifest and storage ownership

`verdant.manifest.v1` is fixed compact UTF-8 JSON, with sorted object keys,
unescaped Unicode and no trailing newline. Members appear in strictly increasing
sample UUID order. The writer checks exact membership count, interpretation pins,
metadata limits and destination writes; it returns a digest only for complete
output. This is Verdant's fixed Python JSON encoding, not an RFC 8785 claim.

The manifest captures namespace/dataset/candidate/generation identities, source
revision, the immutable policy and validation report, and each exact sample
revision, split, metadata, interpretation and logical artifact identity
(`id`, SHA-256, byte size). Storage placements, object keys and signed URLs are
absent, so placement changes do not change the manifest identity. Version label,
publication group and selection mode are outside its byte identity.

The runner reads source bytes in 1 MiB chunks. Manifest membership is read in
50-member database pages; output writes are at most 16 KiB. A temporary spool
spills to worker scratch above 1 MiB, with a supported total manifest cap of
256 MiB. The complete manifest is scanned during preparation, outside finalization.
These are bounded buffers and a supported artifact limit, not a dataset-scale
latency or throughput guarantee.

`PublicationRunner` performs trusted synchronous Python work through the existing
API/MCP composition. It adds no service or native Prefect publication deployment.
Storage I/O occurs outside registry transactions. The worker-only claim/accept
methods are an internal trust boundary: public requests cannot submit readiness,
verification timestamps, artifact digests or accepted storage proofs.

Deployment composition reuses the accepted ingestion `S3Storage.from_env()` and
the same injected storage instance/lifespan for ingestion, preview and publication.
It honors the existing endpoint, bucket, credentials, browser endpoint, source
prefix, addressing style and `VERDANT_S3_REGION` settings. `configured_storage()`
delegates to that factory and fails when storage is unconfigured; it does not
define a second configuration profile. Plain HTTP still requires the local-only
`VERDANT_INSECURE_LOCAL_HTTP=1` setting. Injected `S3Storage` is supported
for owned application/test composition. Credential values stay out of logs/docs.

## Atomic commit and collection

Preparation verifies complete retained source membership and the manifest before
setting `prepared`; database guards enforce that transition. Finalization locks
the dataset, draft, group and operation, reloads current grants after lock waits,
and uses the current database clock for expiry. It checks active dataset
availability, the exact sealed candidate and report/policy gates, fixed mode and
label uniqueness. It then commits the version, published group, succeeded operation,
result-bearing operation event, audit and outbox together in one transaction.
An audit/event/outbox failure rolls back the version and terminal state.

Finalization uses a fixed set of aggregate reads/writes and no remote storage I/O,
generation scan or reference promotion. Generation/reference scans belong to
readiness preparation. The query proof compares application SQL for 1 and 100
members and inspects the finalization trigger branch; it does not measure internal
trigger latency or certify production capacity.

Retention creation and artifact collection serialize through the artifact row.
The collector also refuses artifacts bound to registered sample revisions or
active ingestion. Releasing a group therefore does not authorize deleting accepted
source data: its registered bindings remain conservative protection, and no source
deletion policy is introduced. Collection supports at most 100 placements, all in
its configured store/bucket; other placement sets conflict conservatively. Unknown
delete outcomes leave durable `deleting` state for replay and missing-object
readback before recording `deleted`. Published references cannot be removed.

## REST, MCP and managed Python

REST paths below are relative to
`/api/v1/namespaces/{namespace}/datasets/{dataset}`. Use the existing verified
browser session plus CSRF protection, or the configured service token path.

| Request | Body / behavior |
| --- | --- |
| `POST /candidates/{candidate}/publications` | `{ "request_key": "release-1-attempt", "label": "release-1", "report_id": "<report-uuid>", "mode": "selected_candidate" }`; returns the durable publication view, HTTP 202. |
| `GET /candidates/{candidate}/publications` | Cursor page: `limit` 1–100, default 25; optional UUID `after`. Returns `items` and `next_after`. |
| `GET /publications/{publication}` | Read the current saved state and committed version identity. |
| `POST /publications/{publication}/prepare` | `{ "expected_count": 0, "limit": 25 }`; send the last returned count next time. |
| `POST /publications/{publication}/finalize` | `{}`; returns the committed result or a conflict retaining preparation. |
| `POST /publications/{publication}/renew` | `{}`; only expired, unreleased preparation; all sources must be checked again. |
| `POST /publications/{publication}/release` | `{ "limit": 100 }`; repeat while `releasing`. |

The matching MCP tools are `publication_start`, `publications_list`,
`publication_get`, `publication_prepare`, `publication_finalize`, `publication_renew`
and `publication_release`. They use the same command models and namespace/dataset/
candidate or publication IDs. MCP uses the existing verified service identity;
browser cookies do not authenticate MCP. REST/MCP prepare runs the trusted runner,
not a client-supplied storage-proof callback.

Managed Python uses the same services with an already verified principal and
owned session/storage composition:

```python
from verdant.publication import PublicationBatch, PublicationService, PublicationStart
from verdant.publication_runner import PublicationRunner

service = PublicationService(sessions, bucket=storage.settings.bucket)
runner = PublicationRunner(sessions, storage)
group = service.start(
    actor,
    namespace_id,
    dataset_id,
    candidate_id,
    PublicationStart(
        request_key="release-1-attempt",
        label="release-1",
        report_id=report_id,
        mode="selected_candidate",
    ),
)
while group.state == "building":
    group = runner.prepare(
        actor,
        namespace_id,
        dataset_id,
        group.id,
        PublicationBatch(expected_count=group.prepared_count, limit=25),
    )
# Inspect evidence and handle any conflict before explicitly committing.
group = service.finalize(actor, namespace_id, dataset_id, group.id)
```

Keep the request key for that logical attempt. After interruption, read the saved
group before resuming. A `Principal` object is not authentication; callers must
use the established identity verification path, and services reload current grants.

## Migration and delivery boundary

This stack follows accepted ingestion through `0018_ingestion_sources`, quality
`0019_quality`, then publication `0020_publication`. Startup never upgrades schema.
The unmerged development publication revision `0017_publication` was resequenced;
its isolated databases remain retained outside this canonical upgrade chain.
Run the existing explicit Alembic deployment process. The migration adds
publication groups, references and immutable versions, guarded artifact collection
state and a dataset availability column used by finalization. That column does
not complete #18 archive/admission/consumption behavior.

For application rollback, preserve the additive schema and data. Explicit schema
downgrade drops publication/version/reference records and the new state columns;
it needs a backup and deliberate operator approval. It neither undoes external
object writes nor supplies a safe reset or garbage-collection plan.

Accepted #14 landed through PR #53. This implementation does not establish native Prefect
publication dispatch, #18 exact-version consumption/archive completion, production
capacity or default-branch delivery. The #15 owner retains its independent browser
acceptance gate. See [original qualification](../proofs/issue-17/publication.md) and
[accepted-ingestion reconciliation](../proofs/issue-17/ingestion-reconciliation.md)
for the exact tested stacks and remaining gates.
