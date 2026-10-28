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


def preparation_fixture(registry, sessions):
    registry_service, actor, ns = registry
    dataset = registry_service.create_dataset(
        actor, ns, DatasetCreate(name="Preparation", slug="preparation", modality="text")
    )
    samples = []
    for i in range(3):
        sample = registry_service.create_sample(
            actor,
            ns,
            SampleCreate(
                source_key=f"prepare-{i}",
                modality="text",
                object_key=f"private/prepare/{i}",
                sha256="b" * 64,
                size_bytes=1,
            ),
        )
        registry_service.set_membership(
            actor,
            ns,
            dataset.id,
            sample.sample_id,
            MembershipWrite(expected_revision=i, sample_revision_id=sample.id, split="train"),
        )
        samples.append(sample)
    service = VersioningService(sessions)
    captured = service.capture(
        actor, ns, dataset.id, CandidateCapture(request_key="prepare", expected_revision=3)
    )
    return registry_service, service, actor, ns, dataset, samples, captured


def test_bounded_preparation_resumes_original_revision_while_draft_advances(registry, sessions):
    from verdant.persistence import GenerationMemberRow
    from verdant.versioning import CandidatePrepare

    registry_service, service, actor, ns, dataset, samples, captured = preparation_fixture(
        registry, sessions
    )
    first = service.prepare(
        actor, ns, dataset.id, captured.id, CandidatePrepare(expected_count=0, limit=1)
    )
    assert first.prepared_count == 1 and first.state == "preparing"
    registry_service.set_membership(
        actor,
        ns,
        dataset.id,
        samples[0].sample_id,
        MembershipWrite(expected_revision=3, sample_revision_id=samples[0].id, split="validation"),
    )
    second = service.prepare(
        actor, ns, dataset.id, captured.id, CandidatePrepare(expected_count=1, limit=1)
    )
    assert second.prepared_count == 2 and second.latest_draft_revision == 4
    repeated = service.prepare(
        actor, ns, dataset.id, captured.id, CandidatePrepare(expected_count=1, limit=1)
    )
    assert repeated == second
    sealed = service.prepare(
        actor, ns, dataset.id, captured.id, CandidatePrepare(expected_count=2, limit=1)
    )
    assert sealed.prepared_count == 3 and sealed.state == "sealed"
    assert sealed.source_revision == 3 and sealed.latest_draft_revision == 4
    with sessions() as session:
        rows = session.scalars(
            select(GenerationMemberRow).where(
                GenerationMemberRow.generation_id == captured.generation_id
            )
        ).all()
        assert {(r.sample_id, r.sample_revision_id, r.split) for r in rows} == {
            (s.sample_id, s.id, "train") for s in samples
        }
        assert all(
            r.interpretation
            == {
                "schema": "verdant.membership.v1",
                "annotation_revision": None,
                "task_schema_revision": None,
                "review_evidence": None,
                "transformation": None,
            }
            for r in rows
        )


def test_generation_rows_and_sealed_progress_are_database_immutable(registry, sessions):
    from sqlalchemy.exc import DBAPIError

    from verdant.persistence import GenerationMemberRow
    from verdant.versioning import CandidatePrepare

    _, service, actor, ns, dataset, samples, captured = preparation_fixture(registry, sessions)
    sealed = service.prepare(actor, ns, dataset.id, captured.id, CandidatePrepare(expected_count=0))
    assert sealed.state == "sealed"
    with pytest.raises(DBAPIError, match="membership is immutable"):
        with sessions.begin() as session:
            session.get(
                GenerationMemberRow, (captured.generation_id, samples[0].sample_id)
            ).split = "test"
    with pytest.raises(DBAPIError, match="membership is immutable"):
        with sessions.begin() as session:
            session.delete(
                session.get(GenerationMemberRow, (captured.generation_id, samples[0].sample_id))
            )
    with pytest.raises(DBAPIError, match="no longer accepts"):
        with sessions.begin() as session:
            session.add(
                GenerationMemberRow(
                    generation_id=captured.generation_id,
                    namespace_id=ns,
                    sample_id=samples[0].sample_id,
                    sample_revision_id=samples[0].id,
                    split="train",
                    interpretation={},
                )
            )
    with pytest.raises(DBAPIError, match="sealed candidate is immutable"):
        with sessions.begin() as session:
            session.get(CandidateRow, captured.id).state = "preparing"


def test_competing_preparers_commit_only_one_expected_batch(registry, sessions):
    from verdant.versioning import CandidatePrepare

    _, service, actor, ns, dataset, _, captured = preparation_fixture(registry, sessions)
    barrier = Barrier(2)

    def prepare(_):
        barrier.wait(timeout=5)
        return service.prepare(
            actor, ns, dataset.id, captured.id, CandidatePrepare(expected_count=0, limit=1)
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(prepare, range(2))
    assert first == second and first.prepared_count == 1


def test_preparation_audit_failure_rolls_back_rows_and_progress(registry, sessions):
    from verdant.persistence import GenerationMemberRow
    from verdant.versioning import CandidatePrepare

    _, service, actor, ns, dataset, _, captured = preparation_fixture(registry, sessions)

    def fail(*_):
        raise RuntimeError("preparation audit failed")

    event.listen(AuditRow, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="preparation audit failed"):
            service.prepare(actor, ns, dataset.id, captured.id, CandidatePrepare(expected_count=0))
    finally:
        event.remove(AuditRow, "before_insert", fail)
    with sessions() as session:
        assert session.get(CandidateRow, captured.id).prepared_count == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(GenerationMemberRow)
                .where(GenerationMemberRow.generation_id == captured.generation_id)
            )
            == 0
        )


def test_candidate_and_generation_reads_are_scoped_paginated_and_current_authorized(
    registry, sessions, principal_factory
):
    from sqlalchemy import delete

    from verdant.persistence import NamespaceGrantRow
    from verdant.versioning import CandidatePrepare

    _, service, actor, ns, dataset, samples, captured = preparation_fixture(registry, sessions)
    service.prepare(actor, ns, dataset.id, captured.id, CandidatePrepare(expected_count=0))
    reader = principal_factory(ns, access="read")
    assert service.list_candidates(reader, ns, dataset.id).items[0].id == captured.id
    first = service.members(reader, ns, dataset.id, captured.id, limit=2)
    last = service.members(reader, ns, dataset.id, captured.id, after=first.next_after, limit=2)
    assert len(first.items) == 2 and last.next_after is None
    assert {m.sample_id for m in first.items + last.items} == {s.sample_id for s in samples}
    with pytest.raises(Conflict):
        service.prepare(actor, ns, dataset.id, captured.id, CandidatePrepare(expected_count=4))
    with sessions.begin() as session:
        session.execute(
            delete(NamespaceGrantRow).where(NamespaceGrantRow.principal_id == reader.id)
        )
    for read in (
        lambda: service.get(reader, ns, dataset.id, captured.id),
        lambda: service.get(reader, ns, dataset.id, uuid4()),
        lambda: service.list_candidates(reader, ns, dataset.id),
        lambda: service.members(reader, ns, dataset.id, captured.id),
    ):
        with pytest.raises(Forbidden):
            read()


def test_explicit_current_draft_conflict_preserves_selected_generation(registry, sessions):
    from verdant.versioning import CandidatePrepare, CandidateSelection

    registry_service, service, actor, ns, dataset, samples, captured = preparation_fixture(
        registry, sessions
    )
    with pytest.raises(Conflict, match="preparation must finish"):
        service.check_selection(
            actor, ns, dataset.id, captured.id, CandidateSelection(mode="selected_candidate")
        )
    sealed = service.prepare(actor, ns, dataset.id, captured.id, CandidatePrepare(expected_count=0))
    service.check_selection(
        actor, ns, dataset.id, captured.id, CandidateSelection(mode="current_draft")
    )
    registry_service.set_membership(
        actor,
        ns,
        dataset.id,
        samples[0].sample_id,
        MembershipWrite(expected_revision=3, sample_revision_id=samples[0].id, split="test"),
    )
    with pytest.raises(Conflict, match="Preparation is retained"):
        service.check_selection(
            actor, ns, dataset.id, captured.id, CandidateSelection(mode="current_draft")
        )
    selected = service.check_selection(
        actor, ns, dataset.id, captured.id, CandidateSelection(mode="selected_candidate")
    )
    assert selected.generation_id == sealed.generation_id
    assert selected.source_revision == 3 and selected.latest_draft_revision == 4
    assert selected.prepared_count == 3 and selected.state == "sealed"


def test_nonnull_interpretation_pins_are_retained_from_captured_interval(registry, sessions):
    from sqlalchemy.exc import DBAPIError

    from verdant.contracts import InlineEvidence, InterpretationPins, ReviewPin, TransformationPin
    from verdant.persistence import GenerationMemberRow, MembershipRow
    from verdant.versioning import CandidatePrepare

    registry_service, service, actor, ns, dataset, samples, _ = preparation_fixture(
        registry, sessions
    )
    annotation = InlineEvidence(format="qualification.annotation.v1", payload={"text": "Original"})
    pins = InterpretationPins(
        annotation_revision=annotation,
        task_schema_revision=InlineEvidence(
            format="qualification.schema.v1", payload={"label": "transcription"}
        ),
        review_evidence=ReviewPin(
            annotation_digest=annotation.sha256,
            decision="accepted",
            rationale="Recorded input review; not a policy gate",
        ),
        transformation=TransformationPin(
            payload={
                "code": "text.strip()",
                "configuration": {"strip": True},
                "environment": {"python": "3.12"},
            }
        ),
    )
    registry_service.set_membership(
        actor,
        ns,
        dataset.id,
        samples[0].sample_id,
        MembershipWrite(
            expected_revision=3,
            sample_revision_id=samples[0].id,
            split="train",
            interpretation=pins,
        ),
    )
    captured = service.capture(
        actor, ns, dataset.id, CandidateCapture(request_key="with-pins", expected_revision=4)
    )
    replacement_annotation = InlineEvidence(
        format="qualification.annotation.v1", payload={"text": "Changed"}
    )
    replacement = InterpretationPins(
        annotation_revision=replacement_annotation,
        task_schema_revision=InlineEvidence(
            format="qualification.schema.v2", payload={"label": "document-layout"}
        ),
        review_evidence=ReviewPin(
            annotation_digest=replacement_annotation.sha256,
            decision="rejected",
            rationale="Changed input review",
        ),
        transformation=TransformationPin(
            payload={"code": "text.upper()", "configuration": {}, "environment": {"python": "3.12"}}
        ),
    )
    registry_service.set_membership(
        actor,
        ns,
        dataset.id,
        samples[0].sample_id,
        MembershipWrite(
            expected_revision=4,
            sample_revision_id=samples[0].id,
            split="test",
            interpretation=replacement,
        ),
    )
    sealed = service.prepare(actor, ns, dataset.id, captured.id, CandidatePrepare(expected_count=0))
    assert sealed.source_revision == 4 and sealed.latest_draft_revision == 5
    with sessions() as session:
        frozen = session.get(GenerationMemberRow, (captured.generation_id, samples[0].sample_id))
        assert frozen.interpretation == pins.model_dump(by_alias=True)
        assert frozen.split == "train"
    with pytest.raises(DBAPIError, match="Only an open membership interval can be closed"):
        with sessions.begin() as session:
            row = session.get(MembershipRow, (dataset.draft_id, samples[0].sample_id, 5))
            row.valid_to = 6
            row.interpretation = pins.model_dump(by_alias=True)
    # An ordinary split-only edit cannot silently erase previously recorded interpretation.
    registry_service.set_membership(
        actor,
        ns,
        dataset.id,
        samples[0].sample_id,
        MembershipWrite(expected_revision=5, sample_revision_id=samples[0].id, split="validation"),
    )
    page = registry_service.list_memberships(actor, ns, dataset.id)
    assert (
        next(m for m in page.members if m.sample_id == samples[0].sample_id).interpretation
        == replacement
    )

    from verdant.contracts import SampleRevise

    changed_sample = registry_service.revise_sample(
        actor,
        ns,
        samples[0].sample_id,
        SampleRevise(
            expected_revision=1,
            sha256="c" * 64,
            object_key="private/changed-revision",
            size_bytes=1,
        ),
    )
    with pytest.raises(Conflict, match="explicit replacement"):
        registry_service.set_membership(
            actor,
            ns,
            dataset.id,
            samples[0].sample_id,
            MembershipWrite(
                expected_revision=6, sample_revision_id=changed_sample.id, split="test"
            ),
        )
    registry_service.set_membership(
        actor,
        ns,
        dataset.id,
        samples[0].sample_id,
        MembershipWrite(
            expected_revision=6,
            sample_revision_id=changed_sample.id,
            split="test",
            interpretation=InterpretationPins(),
        ),
    )
