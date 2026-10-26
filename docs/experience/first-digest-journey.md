# First dataset publication journey

This target journey replaces the former first-digest journey. It is acceptance guidance, not a runnable current walkthrough.

1. Sign in and select an authorized namespace. Discover existing datasets or create one with owner, modality, metadata and policy; no experiment or annotation project is required.
2. Open a draft and register verified source objects. Start ingestion; retain the operation ID and see durable progress while Prefect/DataChain process bulk data directly in storage.
3. Inspect exact sample revisions and lineage. If annotation is required, send pinned revisions/schema to Label Studio and import reviewed canonical annotation revisions.
4. Select membership and splits. Validate a frozen candidate against explicit schema/quality policy; show Evidently reports when that integration is available. Resolve failures by changing the draft and validating again.
5. Publish once all gates pass. Inspect version label, manifest digest, schema and revision references. Publish the selected frozen candidate even if the draft has advanced; show both revisions. An explicit current-draft request reports a conflict instead of silently changing its target.
6. Retrieve the authorized version with SDK/REST/MCP. Verify manifest/content digests. Attach MLflow experiments/models to this exact dataset version.
7. Correct a label by creating a new annotation revision and draft/version. Re-read the first published manifest and prove it is unchanged.

Failure cases: access denied, stale draft, failed gate, missing object, disconnected progress feed, duplicate callback, unknown provider outcome and cancellation request. Each must preserve truth and offer an appropriate retry/recovery action.
