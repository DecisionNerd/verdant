from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select
from sqlalchemy.exc import DBAPIError

from verdant.contracts import SampleCreate, SampleRevise
from verdant.domain import Conflict, Forbidden, NotFound
from verdant.ingestion import IngestionService, UploadDeclaration, UploadRequest
from verdant.persistence import (
    ArtifactLocationRow,
    ArtifactRow,
    AuditRow,
    IngestionItemRow,
    NamespaceRow,
    OperationEventRow,
    OperationRow,
    OutboxRow,
    SampleArtifactRow,
)


def declaration(**changes):
    return UploadDeclaration(
        **(
            {"source_key": "source", "modality": "text", "sha256": "a" * 64, "size_bytes": 1}
            | changes
        )
    )


def test_competing_admission_commits_one_owned_batch_and_operation(registry, sessions):
    _, actor, ns = registry
    service = IngestionService(sessions, bucket="private-artifacts")
    command = UploadRequest(request_key=str(uuid4()), items=[declaration()])
    barrier = Barrier(2)

    def admit(_):
        barrier.wait(timeout=5)
        return service.admit(actor, ns, command)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(admit, range(2))
    assert first == second and first.operation.status == "running"
    assert first.items[0].state == "awaiting_upload"
    assert "staging_key" not in first.items[0].model_dump()
    with sessions() as session:
        item = session.get(IngestionItemRow, first.items[0].id)
        artifact = session.get(ArtifactRow, item.artifact_id)
        assert artifact.verified_at is None
        location = session.scalar(
            select(ArtifactLocationRow).where(ArtifactLocationRow.artifact_id == artifact.id)
        )
        assert location.bucket == "private-artifacts" and location.store_id == "primary"
        assert location.object_key == f"{ns}/artifacts/{artifact.id}"
        assert item.staging_key == f"{ns}/staging/{item.id}"
        assert (
            session.scalar(
                select(func.count())
                .select_from(IngestionItemRow)
                .where(IngestionItemRow.operation_id == first.operation.id)
            )
            == 1
        )
        events = session.scalars(
            select(OperationEventRow).where(OperationEventRow.operation_id == first.operation.id)
        ).all()
        assert len(events) == 1 and session.get(OutboxRow, events[0].id) is not None
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditRow)
                .where(AuditRow.aggregate_id == first.operation.id)
            )
            == 1
        )
    with pytest.raises(Conflict, match="different inputs"):
        service.admit(
            actor,
            ns,
            UploadRequest(request_key=command.request_key, items=[declaration(sha256="b" * 64)]),
        )


def test_item_failure_rolls_back_entire_admission(registry, sessions):
    _, actor, ns = registry
    service = IngestionService(sessions, bucket="private-artifacts")

    def fail(*_):
        raise RuntimeError("item persistence failed")

    event.listen(IngestionItemRow, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="item persistence"):
            service.admit(actor, ns, UploadRequest(request_key="rollback", items=[declaration()]))
    finally:
        event.remove(IngestionItemRow, "before_insert", fail)
    with sessions() as session:
        for model in [
            OperationRow,
            OperationEventRow,
            ArtifactRow,
            ArtifactLocationRow,
            IngestionItemRow,
        ]:
            assert (
                session.scalar(
                    select(func.count()).select_from(model).where(model.namespace_id == ns)
                )
                == 0
            )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditRow)
                .where(AuditRow.namespace_id == ns, AuditRow.action == "operation.admitted")
            )
            == 0
        )


def test_ingestion_current_grants_and_namespace_scoping(registry, sessions, principal_factory):
    _, actor, ns = registry
    service = IngestionService(sessions, bucket="private-artifacts")
    reader = principal_factory(ns, access="read")
    command = UploadRequest(request_key="grant", items=[declaration()])
    with pytest.raises(Forbidden):
        service.admit(reader, ns, command)
    batch = service.admit(actor, ns, command)
    assert service.get(reader, ns, batch.operation.id) == batch
    with pytest.raises(Forbidden):
        service.get(principal_factory(), ns, batch.operation.id)
    other = uuid4()
    with sessions.begin() as session:
        session.add(NamespaceRow(id=other, name="other"))
    other_actor = principal_factory(other)
    with pytest.raises(NotFound):
        service.get(other_actor, other, batch.operation.id)
    from verdant.identity import IdentityService

    IdentityService(sessions).revoke_grant(actor.id, ns)
    with pytest.raises(Forbidden):
        service.admit(actor, ns, command)


def test_replacement_retry_keeps_original_declaration_after_sample_advances(registry, sessions):
    registry_service, actor, ns = registry
    sample = registry_service.create_sample(
        actor,
        ns,
        SampleCreate(
            source_key="source", modality="text", sha256="a" * 64, size_bytes=1, object_key="legacy"
        ),
    )
    service = IngestionService(sessions, bucket="private-artifacts")
    command = UploadRequest(
        request_key="replace",
        items=[
            declaration(
                target_sample_id=sample.sample_id,
                expected_revision=1,
            )
        ],
    )
    original = service.admit(actor, ns, command)
    registry_service.revise_sample(
        actor,
        ns,
        sample.sample_id,
        SampleRevise(expected_revision=1, sha256="b" * 64, size_bytes=1, object_key="legacy-2"),
    )
    assert service.admit(actor, ns, command) == original
    with pytest.raises(Conflict, match="revision changed"):
        service.admit(actor, ns, command.model_copy(update={"request_key": "stale"}))


def test_database_guards_declaration_and_unverified_binding(registry, sessions):
    registry_service, actor, ns = registry
    batch = IngestionService(sessions, bucket="private-artifacts").admit(
        actor, ns, UploadRequest(request_key="immutable", items=[declaration()])
    )
    item = batch.items[0]
    with pytest.raises(DBAPIError, match="immutable"):
        with sessions.begin() as session:
            session.get(IngestionItemRow, item.id).source_key = "changed"
    with pytest.raises(DBAPIError, match="immutable"):
        with sessions.begin() as session:
            session.get(ArtifactRow, item.artifact_id).sha256 = "b" * 64
    revision = registry_service.create_sample(
        actor,
        ns,
        SampleCreate(
            source_key="legacy",
            modality="text",
            sha256="a" * 64,
            size_bytes=1,
            object_key=f"{ns}/artifacts/{item.artifact_id}",
        ),
    )
    with pytest.raises(DBAPIError, match="matching verified artifact"):
        with sessions.begin() as session:
            session.add(
                SampleArtifactRow(
                    namespace_id=ns,
                    sample_id=revision.sample_id,
                    revision_id=revision.id,
                    artifact_id=item.artifact_id,
                )
            )
    with pytest.raises(DBAPIError, match="verified sample binding"):
        with sessions.begin() as session:
            stored = session.get(IngestionItemRow, item.id)
            stored.state = "accepted"
            stored.sample_id = revision.sample_id
            stored.accepted_revision_id = revision.id
    with sessions() as session:
        original = session.get(IngestionItemRow, item.id)
        expires_at = original.expires_at
    with pytest.raises(DBAPIError, match="must begin awaiting upload"):
        with sessions.begin() as session:
            session.add(
                IngestionItemRow(
                    namespace_id=ns,
                    operation_id=batch.operation.id,
                    position=1,
                    artifact_id=item.artifact_id,
                    source_key="forged",
                    modality="text",
                    metadata_json={},
                    staging_key=f"{ns}/staging/{uuid4()}",
                    state="accepted",
                    sample_id=revision.sample_id,
                    accepted_revision_id=revision.id,
                    expires_at=expires_at,
                )
            )


def test_batch_contract_limits_and_stable_targets():
    with pytest.raises(ValidationError):
        UploadRequest(request_key="duplicate", items=[declaration(), declaration()])
    with pytest.raises(ValidationError):
        declaration(target_sample_id=uuid4())
    with pytest.raises(ValidationError):
        declaration(size_bytes=True)
    with pytest.raises(ValidationError):
        UploadRequest(request_key="large", items=[declaration(metadata={"text": "é" * 32768})])
    with pytest.raises(ValidationError):
        UploadRequest(
            request_key="too-many", items=[declaration(source_key=str(i)) for i in range(101)]
        )
