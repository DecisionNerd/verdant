"""Configured source registration against actual PostgreSQL and local SeaweedFS."""

import hashlib
from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from verdant.domain import Conflict, Forbidden
from verdant.identity import IdentityService
from verdant.ingestion import (
    IngestionService,
    ReuploadRequest,
    SourceDeclaration,
    SourceRegistrationRequest,
)
from verdant.ingestion_cleanup import IngestionCleanup
from verdant.ingestion_runner import IngestionFailed, IngestionRunner
from verdant.persistence import ArtifactLocationRow, ArtifactRow, OperationRow
from verdant.preview import PreviewService
from verdant.storage import ObjectDigest, S3Storage, StorageUnavailable, configured_source_key


@pytest.fixture
def sources(storage, registry):
    adapter, prefix = storage
    _, actor, ns = registry
    source = S3Storage(replace(adapter.settings, source_prefix=prefix.rstrip("/")))
    service = IngestionService(registry[0].sessions, bucket=source.settings.bucket, storage=source)

    def declare(body=b"source bytes", relative="input.txt", **changes):
        key = configured_source_key(source.settings.source_prefix, ns, relative)
        source.client.put_object(Bucket=source.settings.bucket, Key=key, Body=body)
        item = SourceDeclaration(
            source_key="source",
            modality="text",
            source_object=relative,
            sha256=hashlib.sha256(body).hexdigest(),
            size_bytes=len(body),
            **changes,
        )
        command = SourceRegistrationRequest(request_key=str(uuid4()), items=[item])
        return command, key

    yield service, source, actor, ns, declare
    source.close()


def test_source_is_pinned_verified_copied_and_never_deleted(sources, sessions):
    service, adapter, actor, ns, declare = sources
    command, key = declare(metadata={"origin": "configured source"})
    batch = service.admit_source(actor, ns, command)
    assert batch.operation.kind == "ingest_source" and batch.operation.status == "queued"
    assert batch.items[0].state == "ready" and batch.items[0].source_object == "input.txt"
    assert service.admit_source(actor, ns, command) == batch
    runner = IngestionRunner(sessions, adapter)
    accepted = runner.run(actor, ns, batch.operation.id, 1)
    item = accepted.items[0]
    assert accepted.operation.status == "succeeded" and item.state == "accepted"
    result = PreviewService(sessions, adapter).get(
        actor, ns, item.sample_id, item.accepted_revision_id
    )
    assert result.text == "source bytes"
    IngestionCleanup(sessions, adapter).run(actor, ns, batch.operation.id, 1)
    adapter.verify(key, ObjectDigest(item.sha256, item.size_bytes))
    assert accepted.cleanup_operation_id is not None
    with sessions() as session:
        stored = session.get(OperationRow, batch.operation.id)
        assert stored.inputs["source_scope"] == {
            "store_id": "primary",
            "bucket": adapter.settings.bucket,
            "prefix": adapter.settings.source_prefix,
        }
        assert stored.inputs["items"][0]["metadata"] == {"origin": "configured source"}
    with pytest.raises(DBAPIError), sessions.begin() as session:
        stored = session.get(OperationRow, batch.operation.id)
        stored.inputs = stored.inputs | {"source_scope": {"prefix": "other"}}
        session.flush()
    with pytest.raises(Conflict):
        service.initiate_upload(actor, ns, batch.operation.id, item.id, 1)


@pytest.mark.parametrize("corrupt", [False, True])
def test_missing_corrupt_source_is_explicit_and_retry_retains_identity(sources, sessions, corrupt):
    service, adapter, actor, ns, declare = sources
    command, key = declare()
    batch = service.admit_source(actor, ns, command)
    if corrupt:
        adapter.client.put_object(Bucket=adapter.settings.bucket, Key=key, Body=b"wrong bytes!")
    else:
        adapter.delete_one(key)
    runner = IngestionRunner(sessions, adapter)
    with pytest.raises(
        IngestionFailed, match="artifact_corrupt" if corrupt else "artifact_missing"
    ):
        runner.run(actor, ns, batch.operation.id, 1)
    failed = service.get(actor, ns, batch.operation.id)
    assert failed.operation.status == "failed" and failed.items[0].state == "ready"
    with sessions() as session:
        assert session.get(ArtifactRow, batch.items[0].artifact_id).verified_at is None
    # Privileged fixture repair is not a user-facing source editor.
    adapter.client.put_object(Bucket=adapter.settings.bucket, Key=key, Body=b"source bytes")
    runner.operations.retry(actor, ns, batch.operation.id, 1)
    assert runner.run(actor, ns, batch.operation.id, 2).operation.status == "succeeded"


def test_source_scope_change_denies_acceptance_but_allows_owned_cleanup(sources, sessions):
    service, adapter, actor, ns, declare = sources
    command, key = declare()
    batch = service.admit_source(actor, ns, command)
    old = adapter.settings
    adapter.settings = replace(old, source_prefix="different")
    with pytest.raises(IngestionFailed, match="storage_unavailable"):
        IngestionRunner(sessions, adapter).run(actor, ns, batch.operation.id, 1)
    service.request_cancel(actor, ns, batch.operation.id, 1)
    cleaned = IngestionCleanup(sessions, adapter).run(actor, ns, batch.operation.id, 1)
    assert cleaned.batch.operation.status == "cancelled"
    adapter.verify(key, ObjectDigest(batch.items[0].sha256, batch.items[0].size_bytes))
    adapter.settings = old


def test_unknown_source_promotion_cleanup_retains_final_and_original(
    sources, sessions, monkeypatch
):
    service, adapter, actor, ns, declare = sources
    command, key = declare()
    batch = service.admit_source(actor, ns, command)
    original = adapter.promote

    def lose_response(*args, **kwargs):
        original(*args, **kwargs)
        raise StorageUnavailable("Injected lost promotion response")

    monkeypatch.setattr(adapter, "promote", lose_response)
    with pytest.raises(IngestionFailed):
        IngestionRunner(sessions, adapter).run(actor, ns, batch.operation.id, 1)
    service.request_cancel(actor, ns, batch.operation.id, 1)
    # Disabling source admission must not require source access to retain verified final bytes.
    adapter.settings = replace(adapter.settings, source_prefix=None)
    result = IngestionCleanup(sessions, adapter).run(actor, ns, batch.operation.id, 1)
    assert result.batch.operation.status == "cancelled"
    with sessions() as session:
        final = session.scalar(
            select(ArtifactLocationRow.object_key).where(
                ArtifactLocationRow.artifact_id == batch.items[0].artifact_id
            )
        )
    digest = ObjectDigest(batch.items[0].sha256, batch.items[0].size_bytes)
    adapter.verify(final, digest)
    adapter.verify(key, digest)


def test_source_replacement_and_linked_reupload_preserve_lineage(sources, sessions):
    service, adapter, actor, ns, declare = sources
    command, key = declare()
    runner = IngestionRunner(sessions, adapter)
    first = service.admit_source(actor, ns, command)
    first = runner.run(actor, ns, first.operation.id, 1).items[0]
    replacement, _ = declare(
        b"new bytes", "new.txt", target_sample_id=first.sample_id, expected_revision=1
    )
    next_batch = service.admit_source(actor, ns, replacement)
    revised = runner.run(actor, ns, next_batch.operation.id, 1).items[0]
    assert (
        revised.sample_id == first.sample_id
        and revised.accepted_revision_id != first.accepted_revision_id
    )
    pending, _ = declare(
        b"repair bytes", "repair.txt", target_sample_id=first.sample_id, expected_revision=2
    )
    failed = service.admit_source(actor, ns, pending)
    service.request_cancel(actor, ns, failed.operation.id, 1)
    IngestionCleanup(sessions, adapter).run(actor, ns, failed.operation.id, 1)
    repaired = service.reupload(
        actor, ns, ReuploadRequest(request_key=str(uuid4()), item_ids=[failed.items[0].id])
    )
    assert repaired.operation.kind == "ingest_upload"
    assert repaired.items[0].retry_of_item_id == failed.items[0].id
    assert repaired.items[0].source_object is None
    assert (
        service.get(actor, ns, failed.operation.id).items[0].replacement_operation_id
        == repaired.operation.id
    )


def test_source_denial_precedes_provider_access_and_revocation_fences_acceptance(
    sources, sessions, principal_factory, monkeypatch
):
    service, adapter, actor, ns, declare = sources
    command, _ = declare()
    with pytest.raises(Forbidden):
        service.admit_source(principal_factory(), ns, command)
    batch = service.admit_source(actor, ns, command)
    identities = IdentityService(sessions)
    original = adapter.promote

    def revoke_after_copy(*args):
        result = original(*args)
        identities.revoke_grant(actor.id, ns)
        return result

    monkeypatch.setattr(adapter, "promote", revoke_after_copy)
    with pytest.raises(Forbidden):
        IngestionRunner(sessions, adapter).run(actor, ns, batch.operation.id, 1)
    with sessions() as session:
        assert session.get(ArtifactRow, batch.items[0].artifact_id).verified_at is None


@pytest.mark.parametrize(
    "value",
    [
        "../private",
        "/absolute",
        "a//b",
        "a/./b",
        "a/../b",
        "https://host/x",
        "s3://bucket/x",
        "a%2fb",
        "a?b",
        "a#b",
        "a\\b",
    ],
)
def test_source_urls_and_noncanonical_keys_are_rejected(value):
    with pytest.raises(ValidationError):
        SourceDeclaration(
            source_key="s", modality="text", source_object=value, sha256="a" * 64, size_bytes=1
        )


def test_source_scope_enforces_utf8_key_bytes_and_disabled_admission(sources):
    service, adapter, actor, ns, _ = sources
    command = SourceRegistrationRequest(
        request_key=str(uuid4()),
        items=[
            SourceDeclaration(
                source_key="unicode",
                modality="text",
                source_object="é" * 500,
                sha256="a" * 64,
                size_bytes=1,
            )
        ],
    )
    with pytest.raises(Conflict, match="bounds"):
        service.admit_source(actor, ns, command)
    assert configured_source_key("incoming", ns, "café.txt").endswith("/café.txt")
    adapter.settings = replace(adapter.settings, source_prefix=None)
    with pytest.raises(StorageUnavailable, match="not configured"):
        service.admit_source(actor, ns, command)
