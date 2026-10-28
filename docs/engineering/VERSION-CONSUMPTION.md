# Retained version consumption and availability

The dataset workspace inspects and consumes an exact published version, including
its manifest identity, frozen membership, interpretation and quality evidence.
Deprecation and archive change catalog availability without rewriting that content
or releasing its retention. This is the bounded #18 implementation on the current
development dependency stack. See [qualification](../proofs/issue-18/consumption.md)
for exact source, environment, browser evidence and delivery limits; implementation
does not imply issue closure or default-branch delivery.

## Inspect and consume

Open **Published dataset versions** in the dataset workspace and select an exact
version. The view presents the manifest digest/size and its pinned candidate,
generation and validation report. **Show archived versions** includes versions
hidden from the default list. Deprecated versions remain visible with a warning.
**Show archived datasets** performs the corresponding catalog filter for datasets.

The member page contains exact sample revision identities, frozen metadata,
interpretation pins, logical artifacts and the publication's policy/report.
Its recorded lineage graph has a text/table alternative. Annotation, task-schema,
review and transformation pins that were not supplied in the snapshot are labeled
`absent`; the reader does not invent relationships or fetch current external state
to fill historical gaps. `lineage_status` describes the **current page**, not a
complete graph audit of the whole version. An unannotated sample can legitimately
have partial recorded lineage.

Manifest descriptors, membership and lineage come from authoritative retained SQL
records. They identify promised content; they do not assert that remote bytes are
currently available. **Preview** and **Download exact sample/manifest** read and
verify actual storage bytes. Missing content and digest failures remain explicit
errors rather than successful empty previews. Later draft edits, metadata
corrections, policy revisions and availability changes leave the old manifest and
its frozen member interpretation unchanged.

## Verified reads, placement and limits

Every artifact lookup is bound to an authorized version and either its manifest
or an exact member. There is no unscoped artifact-ID download command. Logical
artifact IDs, digests and manifest bytes exclude storage locations and signed URLs.
The resolver checks at most **four** locations in its configured store/bucket,
ordered by location UUID. Missing, corrupt or unavailable copies can fall back to
another checked placement only after exact size/SHA-256 verification. A fifth
location is outside this bounded attempt; failure does not establish the state of
unchecked copies or qualify another backend.

The supported artifact limit is **256 MiB**. Reads hash the entire object in
**1 MiB** chunks before returning success, even for a small preview or a download
redirect. A text preview retains only the first **4 KiB of bytes**, decoding UTF-8
without displaying an incomplete trailing character. Supported declared modalities
are `text`, `json`, `csv`, `markdown`, `application/json` and `text/*`. Invalid UTF-8,
NUL-containing content and other modalities return an explicit binary/unsupported
preview result; a verified download remains available. No image/PDF/media renderer
or arbitrary binary preview is implied.

Native manifest streaming verifies into a temporary spool that spills to worker
scratch above **1 MiB**, then rechecks access before copying to the destination.
Failed alternative copies reset the spool. Destination failures propagate and
callers discard any partial destination output. These bounds are not production
throughput, latency or capacity certification.

REST reports missing/corrupt retained bytes as HTTP **409**, unavailable storage or
an unqualified placement as **503**, a sample outside the requested version as
**404**, and denied current namespace access as **403**. The configured identity
layer handles unauthenticated/expired requests. Error responses omit object keys,
credentials and provider payloads. Restore or qualify a verified reachable copy;
do not rewrite the version or its digest to hide a storage failure. Collection
continues to refuse retained artifacts after a verified placement is added.

## Current permission and downloads

Publishing never freezes a reader's entitlement. Reads authorize before rows,
counts, lineage or locations are disclosed. After storage I/O, the service reloads
current identity/grants and takes shared authorization locks before returning a
preview, native stream or download redirect. Revocation during verification
therefore prevents a new successful content result or signed-link issuance.
Read-only principals can inspect and consume; availability commands require a
current write grant. The workspace hides cached protected views after an observed
authorization denial.

Browser downloads use an ordinary authenticated application path. After byte
verification and the access recheck, REST returns **307** to an S3 URL signed for
**120 seconds**, with `Cache-Control: no-store` and
`Referrer-Policy: no-referrer`. Storage signing forces attachment download. The
temporary URL appears only in the redirect; descriptors and MCP JSON contain
application paths rather than signed credentials. Do not persist or log redirect
locations.

An already issued signed URL remains usable until its expiry even if a grant is
revoked afterward. This is a bounded bearer-link lifetime, not immediate revocation
at the object service. The application rechecks access on every new issuance.
Verification describes the checked storage read, not protection from later
out-of-band corruption; consumers can compare the downloaded bytes with the
recorded digest and size.

## Availability, revisions and recovery

| Action | Discovery and admission | Retained content |
| --- | --- | --- |
| Deprecate a version | Remains in the default version list with a warning. Activate clears deprecation. | Exact reads and digests stay unchanged. |
| Archive a version | Hidden from the default version list; explicit inclusion and exact lookup remain available. | No bytes, references or evidence are deleted. |
| Unarchive a version | Restores its previous `active` or `deprecated` state. An archived version must be unarchived before activation/deprecation. | Does not republish or rewrite the manifest. |
| Archive a dataset | Hidden from the default dataset catalog. Pauses metadata/draft edits, new candidate capture, policies, validation and publication admission, and publication finalization. | Exact dataset/version reads and existing checkpoints remain available. |
| Unarchive a dataset | Restores dataset discovery and admission. | Existing preparation may resume; publication still checks expiry, gates and current-draft mode. |

Already admitted candidate preparation, validation and publication preparation can
complete safe checkpoints while the dataset is archived. Publication itself
remains paused until unarchive. An expired publication preparation still needs
the renewal/reverification protocol in the [publication guide](IMMUTABLE-PUBLICATION.md).
Namespace-level sample registration/revision and artifact verification remain
available because they do not mutate an archived draft and may serve other datasets.

Commands use compare-and-swap: dataset availability shares `Dataset.revision`
with catalog metadata; version availability uses its own
`availability_revision`. Send the latest returned revision as `expected_revision`.
A stale value returns **409** and preserves saved state; reload before deciding
whether to retry. Archive/unarchive therefore also makes a previously loaded
metadata form stale. Actual transitions increment the revision and atomically
write `dataset.availability_changed` or `version.availability_changed` audit with
the actor, transport and before/after state. A matching-revision no-op does not
invent another transition. Archive is separate from deletion and never invokes
the collector or releases published protection.

## REST and MCP

Paths below are relative to
`/api/v1/namespaces/{namespace}/datasets/{dataset}`. Use the established verified
browser session plus CSRF for writes, or the configured service token path.

| Request | Result / input |
| --- | --- |
| `GET /versions` | Authorized `items`, filtered `total`, `next_after`; `limit` 1–100, default 25, optional UUID `after` and `include_archived=true`. Deprecated versions are included by default. |
| `GET /versions/{version}` | Exact identity, manifest digest/size and availability/prior state/revision. Archive does not disable this lookup. |
| `GET /versions/{version}/members` | Same cursor bounds; frozen member/artifact/lineage records, exact policy/report and page-scoped `lineage_status`. |
| `GET /versions/{version}/manifest` | `artifact_id`, `sha256`, `size_bytes`, `schema: "verdant.manifest.v1"` and authenticated `download_path`; no signed URL or live-byte claim. |
| `GET /versions/{version}/manifest/download` | Verify the exact manifest, recheck access, then redirect as described above. |
| `GET /versions/{version}/samples/{sample}/preview` | Verified `kind: "text"` or `"binary"`, bounded `text`/`truncated` or explicit `warning`, exact identities/digest and authenticated `download_path`. |
| `GET /versions/{version}/samples/{sample}/download` | Verify the exact member artifact and redirect; samples outside that version are rejected. |
| `POST /availability` | `{ "expected_revision": 0, "action": "archive" }` or `"unarchive"`; returns the dataset. |
| `POST /versions/{version}/availability` | `{ "expected_revision": 0, "action": "deprecate" }`; actions are `deprecate`, `activate`, `archive`, `unarchive`; returns the version. |

The namespace dataset list also accepts `include_archived=true`. Both catalogs
filter current access before counts and rows; UUID cursors are not a promise of
chronological ordering.

Matching MCP tools are `versions_list`, `version_get`, `version_members`,
`version_manifest`, `version_sample_preview`, `version_sample_download`,
`dataset_availability` and `version_availability`. They call the same services
with the verified service principal. `version_sample_download` returns only a
logical artifact descriptor and authenticated application path; binary transfer
uses REST or managed Python. Browser cookies do not authenticate MCP, and tools
do not return signed credentials.

## Managed Python

Use an already verified principal with the owned database/storage composition;
constructing a `Principal` dataclass is not authentication. Services reload current
grants. Storage configuration follows the [publication guide](IMMUTABLE-PUBLICATION.md#manifest-and-storage-ownership).

```python
from verdant.consumption import ConsumptionService, VersionAvailability

reader = ConsumptionService(sessions, storage)
version = reader.get_version(actor, namespace_id, dataset_id, version_id)
page = reader.members(actor, namespace_id, dataset_id, version_id, limit=25)
descriptor = reader.manifest(actor, namespace_id, dataset_id, version_id)

with open("manifest.json", "wb") as destination:
    receipt = reader.stream_manifest(actor, namespace_id, dataset_id, version_id, destination)
assert receipt.sha256 == version.manifest_digest == descriptor.sha256

version = reader.version_availability(
    actor,
    namespace_id,
    dataset_id,
    version_id,
    VersionAvailability(expected_revision=version.availability_revision, action="deprecate"),
)
```

Retain the version UUID/digest for consumption. Availability state is current
catalog information; the version's snapshot remains fixed. Managed callers must
handle current denial, missing/corrupt bytes, storage failures and revision
conflicts rather than treating a metadata descriptor as a verified read.

## Migration and delivery boundary

Additive migration `0018_availability` follows `0017_publication`; it adds version
availability, prior state and revision. Its guard permits only valid availability
transitions while preserving every immutable content field and forbidding version
deletion. Dataset availability was introduced in #17; #18 adds catalog controls,
revision-checked commands and admission enforcement around it. The schema head is
`0018_availability`; startup does not apply migrations automatically.

For application rollback, retain the additive schema and retained data. Older code
may not enforce the new catalog/admission semantics: retaining columns alone does
not preserve those behaviors. Keep compatible readers/commands or deliberately
pause affected admission while resolving compatibility. An explicit schema
downgrade removes version availability state and its revision counter from the
version row, restoring the predecessor's fully immutable version guard. Durable
audit remains. The downgrade
does not unarchive datasets, rewrite manifests, undo external storage actions or
release retention. Such a downgrade needs a backup and a deliberate compatibility
decision; it is not routine recovery from an unavailable provider.

This stack includes development snapshots from separately owned #14 ingestion and
#15 candidate work. Their canonical acceptance/landing remains with those owners;
quality/publication draft PRs #50/#51 and this implementation are not default-branch
delivery. Optional Evidently/provider integration, general binary previews, full
external lineage, cloud capacity and lifecycle epic #3 completion are not claimed
from this bounded consumption slice. See the separate
[qualification receipt](../proofs/issue-18/consumption.md) before making acceptance
or closure claims.
