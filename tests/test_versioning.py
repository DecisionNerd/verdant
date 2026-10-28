from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select

from verdant.contracts import DatasetCreate, MembershipWrite, SampleCreate
from verdant.domain import Conflict, Forbidden
from verdant.persistence import AuditRow, CandidateRow
from verdant.versioning import CandidateCapture, VersioningService


def test_capture_retry_preserves_frozen_source_after_draft_advances(registry, sessions):
    registry_service, actor, ns = registry
    dataset = registry_service.create_dataset(
        actor, ns, DatasetCreate(name="Candidate", slug="candidate", modality="text")
    )
    service = VersioningService(sessions)
    command = CandidateCapture(request_key="capture", expected_revision=0)
    captured = service.capture(actor, ns, dataset.id, command)
    sample = registry_service.create_sample(
        actor,
        ns,
        SampleCreate(
            source_key="candidate",
            modality="text",
            object_key="private/candidate",
            sha256="a" * 64,
            size_bytes=1,
        ),
    )
    registry_service.set_membership(
        actor,
        ns,
        dataset.id,
        sample.sample_id,
        MembershipWrite(expected_revision=0, sample_revision_id=sample.id, split="train"),
    )
    repeated = service.capture(actor, ns, dataset.id, command)
    assert repeated.id == captured.id
    assert repeated.generation_id == captured.generation_id
    assert repeated.source_revision == 0 and repeated.latest_draft_revision == 1
    with pytest.raises(Conflict, match="different inputs"):
        service.capture(actor, ns, dataset.id, command.model_copy(update={"expected_revision": 1}))
    with pytest.raises(Conflict, match="Draft changed"):
        service.capture(actor, ns, dataset.id, command.model_copy(update={"request_key": "stale"}))


def test_competing_capture_is_one_candidate_and_audit(registry, sessions):
    registry_service, actor, ns = registry
    dataset = registry_service.create_dataset(
        actor, ns, DatasetCreate(name="Concurrent", slug="concurrent", modality="text")
    )
    service = VersioningService(sessions)
    command = CandidateCapture(request_key="concurrent", expected_revision=0)
    barrier = Barrier(2)

    def capture(_):
        barrier.wait(timeout=5)
        return service.capture(actor, ns, dataset.id, command)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(capture, range(2))
    assert first.id == second.id
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditRow)
                .where(AuditRow.aggregate_id == first.id, AuditRow.action == "candidate.captured")
            )
            == 1
        )


def test_capture_current_write_access_and_audit_rollback(registry, principal_factory, sessions):
    registry_service, actor, ns = registry
    dataset = registry_service.create_dataset(
        actor, ns, DatasetCreate(name="Rollback", slug="rollback", modality="text")
    )
    service = VersioningService(sessions)
    command = CandidateCapture(request_key=str(uuid4()), expected_revision=0)
    with pytest.raises(Forbidden):
        service.capture(principal_factory(ns, access="read"), ns, dataset.id, command)

    def fail(*_):
        raise RuntimeError("audit failure")

    event.listen(AuditRow, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="audit failure"):
            service.capture(actor, ns, dataset.id, command)
    finally:
        event.remove(AuditRow, "before_insert", fail)
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(CandidateRow)
                .where(CandidateRow.namespace_id == ns)
            )
            == 0
        )


def test_database_rejects_capture_identity_changes_and_deletion(registry, sessions):
    from sqlalchemy.exc import DBAPIError

    registry_service, actor, ns = registry
    dataset = registry_service.create_dataset(
        actor,
        ns,
        DatasetCreate(name="Immutable capture", slug="immutable-capture", modality="text"),
    )
    service = VersioningService(sessions)
    candidate = service.capture(
        actor, ns, dataset.id, CandidateCapture(request_key="immutable", expected_revision=0)
    )
    for field, value in (
        ("source_revision", 1),
        ("generation_id", uuid4()),
        ("request_key", "replacement"),
    ):
        with pytest.raises(DBAPIError, match="identity is immutable"):
            with sessions.begin() as session:
                setattr(session.get(CandidateRow, candidate.id), field, value)
    with pytest.raises(DBAPIError, match="cannot be deleted"):
        with sessions.begin() as session:
            session.delete(session.get(CandidateRow, candidate.id))
    assert (
        service.capture(
            actor, ns, dataset.id, CandidateCapture(request_key="immutable", expected_revision=0)
        ).id
        == candidate.id
    )
