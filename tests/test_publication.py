"""Real PostgreSQL/SeaweedFS publication proofs over owned disposable fixtures.

Source fixtures represent trusted ingestion acceptance only after actual immutable
storage writes/readback. They do not qualify ingestion transports. No reset, trigger
disable, database clock override, or external account is used.
"""

import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, get_ident
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError

from verdant.collection import ArtifactCollector
from verdant.contracts import DatasetCreate, MembershipWrite, SampleCreate
from verdant.domain import Conflict, Forbidden, NotFound
from verdant.identity import IdentityService
from verdant.manifest import write_manifest
from verdant.persistence import (
    ArtifactLocationRow,
    ArtifactRow,
    AuditRow,
    DatasetRow,
    DatasetVersionRow,
    OperationEventRow,
    OperationRow,
    OutboxRow,
    PublicationGroupRow,
    RetentionReferenceRow,
    SampleArtifactRow,
)
from verdant.publication import (
    PublicationBatch,
    PublicationRelease,
    PublicationService,
    PublicationStart,
)
from verdant.publication_runner import PublicationRunner
from verdant.quality import QualityDefinition
from verdant.quality_service import PolicyCreate, QualityService, ValidationBatch, ValidationStart
from verdant.storage import ObjectDigest, ObjectMissing, StorageUnavailable
from verdant.versioning import CandidateCapture, CandidatePrepare, VersioningService


@pytest.fixture
def publication_fixture(registry, sessions, storage):
    registry_service, actor, ns = registry
    adapter, prefix = storage

    def create(count=2, *, bindings=True):
        dataset = registry_service.create_dataset(
            actor,
            ns,
            DatasetCreate(name="Publication", slug="publication-" + uuid4().hex, modality="text"),
        )
        body = b"verified source bytes"
        digest = ObjectDigest(hashlib.sha256(body).hexdigest(), len(body))
        key = prefix + uuid4().hex
        adapter.write_immutable(key, io.BytesIO(body), digest)
        with sessions.begin() as session:
            artifact = ArtifactRow(
                namespace_id=ns,
                sha256=digest.sha256,
                size_bytes=digest.size_bytes,
                verified_at=func.clock_timestamp(),
            )
            session.add(artifact)
            session.flush()
            artifact_id = artifact.id
            session.add(
                ArtifactLocationRow(
                    id=uuid4(),
                    namespace_id=ns,
                    artifact_id=artifact_id,
                    store_id="primary",
                    bucket=adapter.settings.bucket,
                    object_key=key,
                )
            )
        samples = []
        for index in range(count):
            sample = registry_service.create_sample(
                actor,
                ns,
                SampleCreate(
                    source_key="source-" + uuid4().hex,
                    modality="text",
                    object_key=key,
                    sha256=digest.sha256,
                    size_bytes=digest.size_bytes,
                    metadata={"label": "cat", "index": index},
                ),
            )
            if bindings:
                with sessions.begin() as session:
                    session.add(
                        SampleArtifactRow(
                            namespace_id=ns,
                            sample_id=sample.sample_id,
                            revision_id=sample.id,
                            artifact_id=artifact_id,
                        )
                    )
            registry_service.set_membership(
                actor,
                ns,
                dataset.id,
                sample.sample_id,
                MembershipWrite(
                    expected_revision=index, sample_revision_id=sample.id, split="train"
                ),
            )
            samples.append(sample)
        versioning = VersioningService(sessions)
        candidate = versioning.capture(
            actor,
            ns,
            dataset.id,
            CandidateCapture(request_key=str(uuid4()), expected_revision=count),
        )
        candidate = versioning.prepare(
            actor, ns, dataset.id, candidate.id, CandidatePrepare(expected_count=0, limit=100)
        )
        quality = QualityService(sessions)
        policy = quality.create_policy(
            actor,
            ns,
            dataset.id,
            PolicyCreate(
                request_key=str(uuid4()),
                name="publication",
                expected_revision=0,
                definition=QualityDefinition.model_validate(
                    {
                        "rules": [
                            {"id": "count", "kind": "minimum_samples", "minimum": 1},
                            {"id": "label", "kind": "required_metadata", "key": "label"},
                        ]
                    }
                ),
            ),
        )
        report = quality.start(
            actor,
            ns,
            dataset.id,
            candidate.id,
            ValidationStart(request_key=str(uuid4()), policy_id=policy.id),
        )
        report = quality.advance(
            actor, ns, dataset.id, report.id, ValidationBatch(expected_count=0, limit=100)
        )
        assert report.state == "complete" and report.required_rules_pass
        return SimpleNamespace(
            actor=actor,
            ns=ns,
            dataset=dataset,
            samples=samples,
            candidate=candidate,
            artifact_id=artifact_id,
            body=body,
            source_key=key,
            digest=digest,
            registry=registry_service,
            quality=quality,
            policy=policy,
            report=report,
            storage=adapter,
            service=PublicationService(sessions, bucket=adapter.settings.bucket),
            runner=PublicationRunner(sessions, adapter),
        )

    yield create
    # These namespaces/keys exist only in this test's isolated fixtures. Keep append-only
    # PostgreSQL evidence; remove only this namespace's generated manifest objects.
    with sessions() as session:
        keys = session.scalars(
            select(ArtifactLocationRow.object_key).where(ArtifactLocationRow.namespace_id == ns)
        ).all()
    for key in keys:
        if key.startswith(f"namespaces/{ns}/manifests/"):
            adapter.delete_one(key)


def start(fixture, *, mode="selected_candidate", label=None, request_key=None, ttl=None):
    command = PublicationStart(
        request_key=request_key or str(uuid4()),
        label=label or "v-" + uuid4().hex,
        report_id=fixture.report.id,
        mode=mode,
    )

    def short_lifetime(mapper, connection, row):
        if row.namespace_id == fixture.ns:
            row.expires_at = connection.scalar(select(func.clock_timestamp())) + timedelta(
                seconds=ttl
            )

    if ttl is not None:
        event.listen(PublicationGroupRow, "before_insert", short_lifetime)
    try:
        publication = fixture.service.start(
            fixture.actor, fixture.ns, fixture.dataset.id, fixture.candidate.id, command
        )
        return publication, command
    finally:
        if ttl is not None:
            event.remove(PublicationGroupRow, "before_insert", short_lifetime)


def prepare(fixture, publication):
    while publication.state == "building":
        publication = fixture.runner.prepare(
            fixture.actor,
            fixture.ns,
            fixture.dataset.id,
            publication.id,
            PublicationBatch(expected_count=publication.prepared_count, limit=25),
        )
    assert publication.state == "prepared"
    return publication


def scalar_count(session, model, ns):
    return session.scalar(select(func.count()).select_from(model).where(model.namespace_id == ns))


@pytest.mark.parametrize("fault", ["manifest_too_large", "preparation_io_unavailable"])
def test_manifest_capacity_and_scratch_failures_retain_progress_without_false_readiness(
    publication_fixture, sessions, monkeypatch, fault
):
    fixture = publication_fixture()
    group, _ = start(fixture)
    if fault == "manifest_too_large":
        monkeypatch.setattr("verdant.manifest.MAX_ARTIFACT_BYTES", 32)
        expected = "exceeds 256 MiB"
    else:

        def unavailable_scratch(**kwargs):
            raise OSError("private/scratch/path must not be disclosed")

        monkeypatch.setattr("verdant.publication_runner.SpooledTemporaryFile", unavailable_scratch)
        expected = "Worker scratch is unavailable"
    with pytest.raises(Conflict, match=expected) as failure:
        fixture.runner.prepare(
            fixture.actor,
            fixture.ns,
            fixture.dataset.id,
            group.id,
            PublicationBatch(expected_count=0),
        )
    assert "private/scratch" not in str(failure.value)
    current = fixture.service.get(fixture.actor, fixture.ns, fixture.dataset.id, group.id)
    assert current.state == "building" and current.prepared_count == current.expected_count
    assert current.last_error == fault and current.manifest_digest is None
    with sessions() as session:
        assert session.get(RetentionReferenceRow, (group.id, fixture.artifact_id)) is not None
    with pytest.raises(Conflict, match="Complete artifact"):
        fixture.service.finalize(fixture.actor, fixture.ns, fixture.dataset.id, group.id)


def wait_clock(sessions, expires_at):
    deadline = monotonic() + 8
    while True:
        with sessions() as observer:
            if observer.scalar(select(func.clock_timestamp())) >= expires_at:
                return
        assert monotonic() < deadline, "fixture did not reach its real PostgreSQL expiry"


def while_blocked(sessions, model, row_id, action, mutation, *, sql_marker=None):
    """Observe actual PostgreSQL row-lock waiting before the concurrent mutation."""
    holding = sessions()
    holding.begin()
    row = holding.scalar(select(model).where(model.id == row_id).with_for_update())
    holder_pid = holding.scalar(text("SELECT pg_backend_pid()"))
    began = Event()
    waiter = []
    main_thread = get_ident()
    engine = sessions.kw["bind"]
    marker = sql_marker or f"from {model.__tablename__}"

    def observe(connection, cursor, statement, parameters, context, executemany):
        if get_ident() != main_thread and marker in statement.lower() and not waiter:
            waiter.append(connection.connection.driver_connection.info.backend_pid)
            began.set()

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(action)
            try:
                assert began.wait(timeout=5), "concurrent action did not reach its lock query"
                deadline = monotonic() + 5
                while True:
                    with sessions() as observer:
                        blocked = observer.scalar(
                            text("SELECT :holder = ANY(pg_blocking_pids(:waiter))"),
                            {"holder": holder_pid, "waiter": waiter[0]},
                        )
                    if blocked:
                        break
                    assert not future.done(), "action completed without waiting on the fixture lock"
                    assert monotonic() < deadline, "concurrent action did not block"
                mutation(holding, row)
                holding.commit()
            finally:
                holding.rollback()
            return future.result(timeout=10)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
        holding.close()


def test_publication_idempotent_start_and_finalize_atomically_commit_exact_result(
    publication_fixture, sessions
):
    fixture = publication_fixture()
    group, command = start(fixture)
    assert (
        fixture.service.start(
            fixture.actor, fixture.ns, fixture.dataset.id, fixture.candidate.id, command
        )
        == group
    )
    with pytest.raises(Conflict, match="different inputs"):
        fixture.service.start(
            fixture.actor,
            fixture.ns,
            fixture.dataset.id,
            fixture.candidate.id,
            command.model_copy(update={"label": "different"}),
        )
    prepared = prepare(fixture, group)
    with sessions() as session:
        row = session.get(PublicationGroupRow, group.id)
        location = session.scalar(
            select(ArtifactLocationRow).where(
                ArtifactLocationRow.artifact_id == row.manifest_artifact_id
            )
        )
        manifest_key = location.object_key
    spool = io.BytesIO()
    fixture.storage.verify(
        manifest_key,
        ObjectDigest(prepared.manifest_digest, prepared.manifest_size),
        destination=spool,
    )
    encoded = json.loads(spool.getvalue())
    assert encoded["candidate_id"] == str(fixture.candidate.id)
    assert encoded["policy"]["id"] == str(fixture.policy.id)
    assert encoded["validation_report"]["id"] == str(fixture.report.id)
    assert len(encoded["members"]) == 2
    assert encoded["members"][0]["artifact"]["id"] == str(fixture.artifact_id)
    assert "object_key" not in spool.getvalue().decode()
    result = fixture.service.finalize(fixture.actor, fixture.ns, fixture.dataset.id, group.id)
    assert result.state == "published" and result.version_id is not None
    assert (
        fixture.service.finalize(fixture.actor, fixture.ns, fixture.dataset.id, group.id) == result
    )
    with sessions() as session:
        assert scalar_count(session, DatasetVersionRow, fixture.ns) == 1
        version = session.get(DatasetVersionRow, result.version_id)
        assert version.group_id == group.id and version.manifest_digest == prepared.manifest_digest
        assert session.get(OperationRow, group.id).status == "succeeded"
        transition = session.scalar(
            select(OperationEventRow).where(
                OperationEventRow.operation_id == group.id,
                OperationEventRow.event_type == "operation.succeeded",
            )
        )
        assert transition.payload["version_id"] == str(result.version_id)
        assert session.get(OutboxRow, transition.id) is not None
        assert (
            session.scalar(
                select(AuditRow.id).where(
                    AuditRow.namespace_id == fixture.ns,
                    AuditRow.action == "version.published",
                    AuditRow.aggregate_id == result.version_id,
                )
            )
            is not None
        )


def test_finalize_audit_failure_rolls_back_version_group_operation_event_and_outbox(
    publication_fixture, sessions
):
    fixture = publication_fixture()
    group = prepare(fixture, start(fixture)[0])
    with sessions() as session:
        before = scalar_count(session, OperationEventRow, fixture.ns)

    def reject(mapper, connection, row):
        if row.namespace_id == fixture.ns and row.action == "version.published":
            raise RuntimeError("injected publication audit failure")

    event.listen(AuditRow, "before_insert", reject)
    try:
        with pytest.raises(RuntimeError, match="publication audit failure"):
            fixture.service.finalize(fixture.actor, fixture.ns, fixture.dataset.id, group.id)
    finally:
        event.remove(AuditRow, "before_insert", reject)
    with sessions() as session:
        assert session.get(PublicationGroupRow, group.id).state == "prepared"
        assert session.get(OperationRow, group.id).status == "running"
        assert scalar_count(session, DatasetVersionRow, fixture.ns) == 0
        assert scalar_count(session, OperationEventRow, fixture.ns) == before
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxRow)
                .join(OperationEventRow, OutboxRow.event_id == OperationEventRow.id)
                .where(OperationEventRow.namespace_id == fixture.ns)
            )
            == before
        )
    assert (
        fixture.service.finalize(fixture.actor, fixture.ns, fixture.dataset.id, group.id).state
        == "published"
    )


@pytest.mark.parametrize("gate", ["pending", "failed", "unknown", "wrong_candidate"])
def test_only_complete_exact_candidate_passing_required_gates_admit_publication(
    publication_fixture, sessions, gate
):
    fixture = publication_fixture()
    if gate == "wrong_candidate":
        candidate = VersioningService(sessions).capture(
            fixture.actor,
            fixture.ns,
            fixture.dataset.id,
            CandidateCapture(request_key=str(uuid4()), expected_revision=2),
        )
        candidate = VersioningService(sessions).prepare(
            fixture.actor,
            fixture.ns,
            fixture.dataset.id,
            candidate.id,
            CandidatePrepare(expected_count=0),
        )
        report_id = fixture.report.id
    else:
        candidate = fixture.candidate
        rule = (
            {"id": "gate", "kind": "minimum_samples", "minimum": 3}
            if gate == "failed"
            else {"id": "gate", "kind": "required_metadata", "key": "label", "split": "test"}
        )
        policy = fixture.quality.create_policy(
            fixture.actor,
            fixture.ns,
            fixture.dataset.id,
            PolicyCreate(
                request_key=str(uuid4()),
                name="adverse",
                expected_revision=0,
                definition=QualityDefinition.model_validate({"rules": [rule]}),
            ),
        )
        report = fixture.quality.start(
            fixture.actor,
            fixture.ns,
            fixture.dataset.id,
            candidate.id,
            ValidationStart(request_key=str(uuid4()), policy_id=policy.id),
        )
        if gate != "pending":
            report = fixture.quality.advance(
                fixture.actor,
                fixture.ns,
                fixture.dataset.id,
                report.id,
                ValidationBatch(expected_count=0),
            )
            assert not report.required_rules_pass
        report_id = report.id
    with pytest.raises((Conflict, NotFound)):
        fixture.service.start(
            fixture.actor,
            fixture.ns,
            fixture.dataset.id,
            candidate.id,
            PublicationStart(request_key=str(uuid4()), label="adverse", report_id=report_id),
        )
    with sessions() as session:
        assert scalar_count(session, PublicationGroupRow, fixture.ns) == 0
        assert scalar_count(session, OperationRow, fixture.ns) == 0


def test_missing_verified_binding_cannot_become_prepared(publication_fixture):
    fixture = publication_fixture(bindings=False)
    group, _ = start(fixture)
    with pytest.raises(Conflict, match="verified artifact binding"):
        fixture.runner.prepare(
            fixture.actor,
            fixture.ns,
            fixture.dataset.id,
            group.id,
            PublicationBatch(expected_count=0),
        )
    assert (
        fixture.service.get(fixture.actor, fixture.ns, fixture.dataset.id, group.id).prepared_count
        == 0
    )


@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_actual_source_failure_keeps_readiness_false_and_retains_protection(
    publication_fixture, sessions, fault
):
    fixture = publication_fixture()
    group, _ = start(fixture)
    if fault == "missing":
        fixture.storage.delete_one(fixture.source_key)
    else:
        fixture.storage.client.put_object(
            Bucket=fixture.storage.settings.bucket, Key=fixture.source_key, Body=b"corrupted"
        )
    with pytest.raises(Conflict, match="artifact_" + fault):
        fixture.runner.prepare(
            fixture.actor,
            fixture.ns,
            fixture.dataset.id,
            group.id,
            PublicationBatch(expected_count=0),
        )
    current = fixture.service.get(fixture.actor, fixture.ns, fixture.dataset.id, group.id)
    assert (
        current.state == "building"
        and current.prepared_count == 0
        and current.last_error == "artifact_" + fault
    )
    with sessions() as session:
        reference = session.get(RetentionReferenceRow, (group.id, fixture.artifact_id))
        assert reference is not None and reference.verified_at is None
    with pytest.raises(Conflict, match="before publishing"):
        fixture.service.finalize(fixture.actor, fixture.ns, fixture.dataset.id, group.id)


def test_bounded_checkpoint_replay_and_restart_use_exact_original_membership(publication_fixture):
    fixture = publication_fixture(count=3)
    group, _ = start(fixture)
    first = fixture.runner.prepare(
        fixture.actor,
        fixture.ns,
        fixture.dataset.id,
        group.id,
        PublicationBatch(expected_count=0, limit=1),
    )
    assert first.prepared_count == 1 and first.state == "building"
    fresh = PublicationRunner(fixture.runner.publications.sessions, fixture.storage)
    assert (
        fresh.prepare(
            fixture.actor,
            fixture.ns,
            fixture.dataset.id,
            group.id,
            PublicationBatch(expected_count=0, limit=1),
        )
        == first
    )
    second = fresh.prepare(
        fixture.actor,
        fixture.ns,
        fixture.dataset.id,
        group.id,
        PublicationBatch(expected_count=1, limit=1),
    )
    assert second.prepared_count == 2 and second.state == "building"
    last = fresh.prepare(
        fixture.actor,
        fixture.ns,
        fixture.dataset.id,
        group.id,
        PublicationBatch(expected_count=2, limit=1),
    )
    assert last.prepared_count == 3 and last.state == "prepared"


def test_lost_manifest_write_response_reconciles_the_same_protected_identity(
    publication_fixture, sessions, monkeypatch
):
    fixture = publication_fixture(count=1)
    group, _ = start(fixture)
    original = fixture.storage.write_immutable
    attempts = []

    def response_lost(key, source, digest):
        result = original(key, source, digest)
        attempts.append((key, digest))
        if len(attempts) == 1:
            raise StorageUnavailable("injected lost response after actual manifest write")
        return result

    monkeypatch.setattr(fixture.storage, "write_immutable", response_lost)
    with pytest.raises(Conflict, match="storage_unavailable"):
        fixture.runner.prepare(
            fixture.actor,
            fixture.ns,
            fixture.dataset.id,
            group.id,
            PublicationBatch(expected_count=0),
        )
    partial = fixture.service.get(fixture.actor, fixture.ns, fixture.dataset.id, group.id)
    assert partial.prepared_count == 1 and partial.state == "building"
    with sessions() as session:
        manifest_id = session.get(PublicationGroupRow, group.id).manifest_artifact_id
    resumed = PublicationRunner(sessions, fixture.storage).prepare(
        fixture.actor, fixture.ns, fixture.dataset.id, group.id, PublicationBatch(expected_count=1)
    )
    assert resumed.state == "prepared" and len(attempts) == 2 and attempts[0] == attempts[1]
    with sessions() as session:
        assert session.get(PublicationGroupRow, group.id).manifest_artifact_id == manifest_id


@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_manifest_write_without_successful_independent_readback_is_not_ready(
    publication_fixture, sessions, monkeypatch, fault
):
    fixture = publication_fixture(count=1)
    group, _ = start(fixture)
    original = fixture.storage.write_immutable

    def fail_readback(key, source, digest):
        original(key, source, digest)
        if fault == "missing":
            fixture.storage.delete_one(key)
        else:
            fixture.storage.client.put_object(
                Bucket=fixture.storage.settings.bucket, Key=key, Body=b"corrupt manifest"
            )
        return fixture.storage.verify(key, digest)

    monkeypatch.setattr(fixture.storage, "write_immutable", fail_readback)
    with pytest.raises(Conflict, match="artifact_" + fault):
        fixture.runner.prepare(
            fixture.actor,
            fixture.ns,
            fixture.dataset.id,
            group.id,
            PublicationBatch(expected_count=0),
        )
    with sessions() as session:
        row = session.get(PublicationGroupRow, group.id)
        assert row.state == "building" and row.prepared_count == 1
        assert session.get(ArtifactRow, row.manifest_artifact_id).verified_at is None
        assert (
            session.get(RetentionReferenceRow, (row.id, row.manifest_artifact_id)).verified_at
            is None
        )


def test_selected_candidate_survives_draft_edit_while_current_draft_mode_conflicts(
    publication_fixture,
):
    fixture = publication_fixture()
    selected = prepare(fixture, start(fixture)[0])
    current = prepare(fixture, start(fixture, mode="current_draft")[0])
    sample = fixture.samples[0]
    fixture.registry.set_membership(
        fixture.actor,
        fixture.ns,
        fixture.dataset.id,
        sample.sample_id,
        MembershipWrite(expected_revision=2, sample_revision_id=sample.id, split="test"),
    )
    with pytest.raises(Conflict, match="Preparation is retained"):
        fixture.service.finalize(fixture.actor, fixture.ns, fixture.dataset.id, current.id)
    assert (
        fixture.service.get(fixture.actor, fixture.ns, fixture.dataset.id, current.id).state
        == "prepared"
    )
    assert (
        fixture.service.finalize(fixture.actor, fixture.ns, fixture.dataset.id, selected.id).state
        == "published"
    )


def test_new_claim_and_release_fence_old_storage_proofs(publication_fixture):
    fixture = publication_fixture()
    group, _ = start(fixture)
    first = fixture.service.claim_batch(
        fixture.actor,
        fixture.ns,
        fixture.dataset.id,
        group.id,
        PublicationBatch(expected_count=0, limit=1),
    )
    latest = fixture.service.claim_batch(
        fixture.actor,
        fixture.ns,
        fixture.dataset.id,
        group.id,
        PublicationBatch(expected_count=0, limit=1),
    )
    for source in {member.artifact.id: member.artifact for member in first.members}.values():
        fixture.storage.verify(source.object_key, source.digest)
    with pytest.raises(Conflict, match="stale"):
        fixture.service.accept_batch(fixture.actor, fixture.ns, fixture.dataset.id, first)
    result = fixture.service.accept_batch(fixture.actor, fixture.ns, fixture.dataset.id, latest)
    assert result.prepared_count == 1
    released = fixture.service.release(
        fixture.actor, fixture.ns, fixture.dataset.id, group.id, PublicationRelease()
    )
    assert released.state == "released"
    with pytest.raises(Conflict, match="permit preparation"):
        fixture.service.accept_batch(fixture.actor, fixture.ns, fixture.dataset.id, latest)


def test_expired_protection_renewal_resets_progress_and_fences_old_attempt(
    publication_fixture, sessions
):
    fixture = publication_fixture(count=3)
    group, _ = start(fixture, ttl=1)
    partial = fixture.runner.prepare(
        fixture.actor,
        fixture.ns,
        fixture.dataset.id,
        group.id,
        PublicationBatch(expected_count=0, limit=1),
    )
    proof = fixture.service.claim_batch(
        fixture.actor,
        fixture.ns,
        fixture.dataset.id,
        group.id,
        PublicationBatch(expected_count=1, limit=1),
    )
    wait_clock(sessions, partial.expires_at)
    with pytest.raises(Conflict, match="expired"):
        fixture.service.accept_batch(fixture.actor, fixture.ns, fixture.dataset.id, proof)
    renewed = fixture.service.renew(fixture.actor, fixture.ns, fixture.dataset.id, group.id)
    assert renewed.state == "building" and renewed.prepared_count == 0 and not renewed.expired
    with sessions() as session:
        row = session.get(PublicationGroupRow, group.id)
        assert row.cursor is None and row.epoch > proof.epoch
    with pytest.raises(Conflict, match="stale"):
        fixture.service.accept_batch(fixture.actor, fixture.ns, fixture.dataset.id, proof)
    assert prepare(fixture, renewed).state == "prepared"


def test_release_removes_only_own_references_and_published_protection_cannot_release(
    publication_fixture, sessions
):
    fixture = publication_fixture()
    released = prepare(fixture, start(fixture)[0])
    published = prepare(fixture, start(fixture)[0])
    published = fixture.service.finalize(
        fixture.actor, fixture.ns, fixture.dataset.id, published.id
    )
    partial = fixture.service.release(
        fixture.actor, fixture.ns, fixture.dataset.id, released.id, PublicationRelease(limit=1)
    )
    assert partial.state == "releasing"
    final = fixture.service.release(
        fixture.actor, fixture.ns, fixture.dataset.id, released.id, PublicationRelease(limit=1)
    )
    assert final.state == "released"
    with pytest.raises(Conflict, match="before publishing"):
        fixture.service.finalize(fixture.actor, fixture.ns, fixture.dataset.id, released.id)
    with pytest.raises(Conflict, match="cannot be released"):
        fixture.service.release(
            fixture.actor, fixture.ns, fixture.dataset.id, published.id, PublicationRelease()
        )
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(RetentionReferenceRow)
                .where(RetentionReferenceRow.group_id == released.id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(RetentionReferenceRow)
                .where(RetentionReferenceRow.group_id == published.id)
            )
            == 2
        )
    collector = ArtifactCollector(sessions, fixture.storage)
    with pytest.raises(Conflict, match="protected"):
        collector.claim(fixture.actor, fixture.ns, fixture.artifact_id)
    with sessions() as session:
        manifest_id = session.get(PublicationGroupRow, published.id).manifest_artifact_id
    with pytest.raises(Conflict, match="protected"):
        collector.claim(fixture.actor, fixture.ns, manifest_id)


def test_released_unbound_manifest_collection_reconciles_lost_delete_response(
    publication_fixture, sessions, monkeypatch
):
    fixture = publication_fixture()
    group = prepare(fixture, start(fixture)[0])
    with sessions() as session:
        manifest_id = session.get(PublicationGroupRow, group.id).manifest_artifact_id
        key = session.scalar(
            select(ArtifactLocationRow.object_key).where(
                ArtifactLocationRow.artifact_id == manifest_id
            )
        )
    assert (
        fixture.service.release(
            fixture.actor, fixture.ns, fixture.dataset.id, group.id, PublicationRelease()
        ).state
        == "released"
    )
    collector = ArtifactCollector(sessions, fixture.storage)
    original = fixture.storage.delete_one
    calls = []

    def lost_response(key):
        original(key)
        calls.append(key)
        if len(calls) == 1:
            raise StorageUnavailable("injected lost response after actual delete")

    monkeypatch.setattr(fixture.storage, "delete_one", lost_response)
    with pytest.raises(StorageUnavailable):
        collector.collect(fixture.actor, fixture.ns, manifest_id)
    with pytest.raises(ObjectMissing):
        fixture.storage.size(key)
    with sessions() as session:
        assert session.get(ArtifactRow, manifest_id).collection_state == "deleting"
    ArtifactCollector(sessions, fixture.storage).collect(fixture.actor, fixture.ns, manifest_id)
    collector.collect(fixture.actor, fixture.ns, manifest_id)
    assert calls == [key, key]
    with sessions() as session:
        assert session.get(ArtifactRow, manifest_id).collection_state == "deleted"
    # Even after releasing publication, registered source revisions remain protected.
    with pytest.raises(Conflict, match="registered data"):
        collector.claim(fixture.actor, fixture.ns, fixture.artifact_id)


def test_persistence_rejects_forged_ready_and_mutation_of_published_evidence(
    publication_fixture, sessions
):
    fixture = publication_fixture()
    group, _ = start(fixture)
    with pytest.raises(IntegrityError, match="Readiness requires"), sessions.begin() as session:
        session.execute(
            text(
                "UPDATE publication_groups SET state='prepared', prepared_count=expected_count, "
                "cursor=:cursor, epoch=1 WHERE id=:id"
            ),
            {"cursor": max(item.sample_id for item in fixture.samples), "id": group.id},
        )
    published = fixture.service.finalize(
        fixture.actor, fixture.ns, fixture.dataset.id, prepare(fixture, group).id
    )
    for statement, identifier in [
        ("UPDATE publication_groups SET state='releasing' WHERE id=:id", group.id),
        ("UPDATE dataset_versions SET label='changed' WHERE id=:id", published.version_id),
        ("DELETE FROM dataset_versions WHERE id=:id", published.version_id),
        ("DELETE FROM retention_references WHERE group_id=:id", group.id),
        ("UPDATE artifacts SET collection_state='deleting' WHERE id=:id", fixture.artifact_id),
    ]:
        with pytest.raises(IntegrityError), sessions.begin() as session:
            session.execute(text(statement), {"id": identifier})


def test_revoke_while_preparation_waits_for_group_lock_is_rechecked(publication_fixture, sessions):
    fixture = publication_fixture()
    group, _ = start(fixture)
    with pytest.raises(Forbidden):
        while_blocked(
            sessions,
            PublicationGroupRow,
            group.id,
            lambda: fixture.service.claim_batch(
                fixture.actor,
                fixture.ns,
                fixture.dataset.id,
                group.id,
                PublicationBatch(expected_count=0),
            ),
            lambda holding, row: IdentityService(sessions).revoke_grant(
                fixture.actor.id, fixture.ns
            ),
        )
    with sessions() as session:
        assert session.get(PublicationGroupRow, group.id).epoch == 0
        assert scalar_count(session, RetentionReferenceRow, fixture.ns) == 0


def test_revoke_while_finalization_waits_for_dataset_lock_is_rechecked(
    publication_fixture, sessions
):
    fixture = publication_fixture()
    group = prepare(fixture, start(fixture)[0])
    with pytest.raises(Forbidden):
        while_blocked(
            sessions,
            DatasetRow,
            fixture.dataset.id,
            lambda: fixture.service.finalize(
                fixture.actor, fixture.ns, fixture.dataset.id, group.id
            ),
            lambda holding, row: IdentityService(sessions).revoke_grant(
                fixture.actor.id, fixture.ns
            ),
        )
    with sessions() as session:
        assert session.get(PublicationGroupRow, group.id).state == "prepared"
        assert scalar_count(session, DatasetVersionRow, fixture.ns) == 0


def test_expiry_while_finalization_waits_for_dataset_lock_uses_fresh_database_clock(
    publication_fixture, sessions
):
    fixture = publication_fixture(count=1)
    group = prepare(fixture, start(fixture, ttl=2)[0])
    with pytest.raises(Conflict, match="expired"):
        while_blocked(
            sessions,
            DatasetRow,
            fixture.dataset.id,
            lambda: fixture.service.finalize(
                fixture.actor, fixture.ns, fixture.dataset.id, group.id
            ),
            lambda holding, row: wait_clock(sessions, group.expires_at),
        )
    with sessions() as session:
        assert session.get(PublicationGroupRow, group.id).state == "prepared"
        assert scalar_count(session, DatasetVersionRow, fixture.ns) == 0


def test_finalization_query_envelope_is_independent_of_generation_size(
    publication_fixture, sessions, monkeypatch, record_property
):
    captures = []
    measurements = []
    engine = sessions.kw["bind"]
    with sessions() as observer:
        postgres_version = int(observer.scalar(text("SHOW server_version_num")))
    for count in [1, 100]:
        fixture = publication_fixture(count=count)
        group = prepare(fixture, start(fixture)[0])
        statements = []
        lock_snapshots = []
        observing = False

        def capture(connection, cursor, statement, parameters, context, executemany):
            if not observing:
                statements.append(" ".join(statement.lower().split()))

        def capture_locks(connection, cursor, statement, parameters, context, executemany):
            nonlocal observing
            if observing or not statement.lower().startswith("insert into operation_outbox"):
                return
            # Observe the finalizer's still-uncommitted transaction from a separate
            # connection. Never capture observer SQL or private parameter values.
            backend = connection.connection.driver_connection.info.backend_pid
            observing = True
            try:
                with engine.connect() as observer:
                    locks = observer.execute(
                        text(
                            "SELECT c.relname, c.relkind, l.mode FROM pg_locks l "
                            "JOIN pg_class c ON c.oid=l.relation "
                            "JOIN pg_namespace n ON n.oid=c.relnamespace "
                            "WHERE l.pid=:backend AND l.locktype='relation' "
                            "AND l.granted AND n.nspname='public' "
                            "ORDER BY c.relname, l.mode"
                        ),
                        {"backend": backend},
                    ).all()
                lock_snapshots.append(
                    [
                        {"relation": relation, "relation_kind": kind, "mode": mode}
                        for relation, kind, mode in locks
                    ]
                )
            finally:
                observing = False

        def no_storage(*args, **kwargs):
            raise AssertionError("finalization performed remote storage I/O")

        with monkeypatch.context() as patch:
            for name in ["verify", "write_immutable", "size", "delete_one", "list_page"]:
                patch.setattr(fixture.storage, name, no_storage)
            event.listen(engine, "before_cursor_execute", capture)
            event.listen(engine, "after_cursor_execute", capture_locks)
            try:
                began = monotonic()
                assert (
                    fixture.service.finalize(
                        fixture.actor, fixture.ns, fixture.dataset.id, group.id
                    ).state
                    == "published"
                )
                elapsed_ms = (monotonic() - began) * 1000
            finally:
                event.remove(engine, "before_cursor_execute", capture)
                event.remove(engine, "after_cursor_execute", capture_locks)
        assert not any(
            table in statement
            for statement in statements
            for table in [
                "generation_members",
                "sample_artifacts",
                "retention_references",
                "artifact_locations",
            ]
        )
        assert len(lock_snapshots) == 1
        table_locks = [lock for lock in lock_snapshots[0] if lock["relation_kind"] in {"r", "p"}]
        index_locks = [lock for lock in lock_snapshots[0] if lock["relation_kind"] in {"i", "I"}]
        measurements.append(
            {
                "member_count": count,
                "application_statement_count": len(statements),
                "relation_lock_count_at_outbox_flush": len(lock_snapshots[0]),
                "relation_locks_at_outbox_flush": lock_snapshots[0],
                "table_lock_count_at_outbox_flush": len(table_locks),
                "table_locks_at_outbox_flush": table_locks,
                "index_lock_count_at_outbox_flush": len(index_locks),
                "index_locks_at_outbox_flush": index_locks,
                "elapsed_finalization_ms_including_observation": round(elapsed_ms, 3),
            }
        )
        captures.append(statements)
    assert captures[0] == captures[1]
    assert (
        measurements[0]["table_locks_at_outbox_flush"]
        == measurements[1]["table_locks_at_outbox_flush"]
    )
    record_property(
        "finalization_measurement",
        json.dumps(
            {
                "schema_version": 1,
                "postgresql_version_num": postgres_version,
                "cases": measurements,
                "all_relation_lock_footprints_equal": int(
                    measurements[0]["relation_locks_at_outbox_flush"]
                    == measurements[1]["relation_locks_at_outbox_flush"]
                ),
                "limits": {
                    "samples_per_size": 1,
                    "observer_time_included": 1,
                    "transaction_protocol_statements_counted": 0,
                    "trigger_internal_statements_counted": 0,
                    "row_lock_identity_measured": 0,
                    "contended_latency_measured": 0,
                    "production_capacity_measured": 0,
                    "index_lock_variation_cause_identified": 0,
                },
            },
            sort_keys=True,
        ),
    )


def unbound_artifact(fixture, sessions):
    key = fixture.source_key + "-unbound-" + uuid4().hex
    fixture.storage.write_immutable(key, io.BytesIO(fixture.body), fixture.digest)
    with sessions.begin() as session:
        artifact = ArtifactRow(
            namespace_id=fixture.ns,
            sha256=fixture.digest.sha256,
            size_bytes=fixture.digest.size_bytes,
            verified_at=func.clock_timestamp(),
        )
        session.add(artifact)
        session.flush()
        identifier = artifact.id
        session.add(
            ArtifactLocationRow(
                id=uuid4(),
                namespace_id=fixture.ns,
                artifact_id=identifier,
                store_id="primary",
                bucket=fixture.storage.settings.bucket,
                object_key=key,
            )
        )
    return identifier, key


def test_collector_waiting_on_artifact_rechecks_new_committed_retention_reference(
    publication_fixture, sessions
):
    fixture = publication_fixture(count=1)
    group, _ = start(fixture)
    artifact_id, key = unbound_artifact(fixture, sessions)
    collector = ArtifactCollector(sessions, fixture.storage)

    def protect(holding, artifact):
        holding.add(
            RetentionReferenceRow(
                namespace_id=fixture.ns, group_id=group.id, artifact_id=artifact_id
            )
        )
        holding.flush()

    with pytest.raises(Conflict, match="protected"):
        while_blocked(
            sessions,
            ArtifactRow,
            artifact_id,
            lambda: collector.claim(fixture.actor, fixture.ns, artifact_id),
            protect,
        )
    fixture.storage.verify(key, fixture.digest)
    with sessions() as session:
        assert session.get(ArtifactRow, artifact_id).collection_state == "active"
        assert session.get(RetentionReferenceRow, (group.id, artifact_id)) is not None


def test_retention_insert_waiting_on_collection_rechecks_deleting_artifact(
    publication_fixture, sessions
):
    fixture = publication_fixture(count=1)
    group, _ = start(fixture)
    artifact_id, key = unbound_artifact(fixture, sessions)

    def insert_reference():
        with sessions.begin() as session:
            session.add(
                RetentionReferenceRow(
                    namespace_id=fixture.ns, group_id=group.id, artifact_id=artifact_id
                )
            )
            session.flush()

    def claim_deletion(holding, artifact):
        # Exercise the same guarded database transition as the collector while its
        # artifact lock is still held, so the competing trigger must wait and recheck.
        artifact.collection_state = "deleting"
        holding.flush()

    with pytest.raises(IntegrityError, match="active artifacts"):
        while_blocked(
            sessions,
            ArtifactRow,
            artifact_id,
            insert_reference,
            claim_deletion,
            sql_marker="insert into retention_references",
        )
    with sessions() as session:
        assert session.get(RetentionReferenceRow, (group.id, artifact_id)) is None
        assert session.get(ArtifactRow, artifact_id).collection_state == "deleting"
    ArtifactCollector(sessions, fixture.storage).collect(fixture.actor, fixture.ns, artifact_id)
    with pytest.raises(ObjectMissing):
        fixture.storage.size(key)


def test_managed_expiry_releases_bounded_references_and_cannot_release_published_group(
    publication_fixture, sessions
):
    fixture = publication_fixture(count=1)
    expiring = prepare(fixture, start(fixture, ttl=1)[0])
    with pytest.raises(Conflict, match="has not expired"):
        fixture.service.expire(
            fixture.actor, fixture.ns, fixture.dataset.id, expiring.id, PublicationRelease(limit=1)
        )
    wait_clock(sessions, expiring.expires_at)
    first = fixture.service.expire(
        fixture.actor, fixture.ns, fixture.dataset.id, expiring.id, PublicationRelease(limit=1)
    )
    assert first.state == "releasing"
    finished = fixture.service.expire(
        fixture.actor, fixture.ns, fixture.dataset.id, expiring.id, PublicationRelease(limit=1)
    )
    assert finished.state == "released"
    with sessions() as session:
        assert session.get(OperationRow, expiring.id).status == "cancelled"
        assert (
            session.scalar(
                select(func.count())
                .select_from(RetentionReferenceRow)
                .where(RetentionReferenceRow.group_id == expiring.id)
            )
            == 0
        )
    with pytest.raises(Conflict, match="cannot be renewed"):
        fixture.service.renew(fixture.actor, fixture.ns, fixture.dataset.id, expiring.id)
    active = prepare(fixture, start(fixture, ttl=1)[0])
    published = fixture.service.finalize(fixture.actor, fixture.ns, fixture.dataset.id, active.id)
    wait_clock(sessions, published.expires_at)
    with pytest.raises(Conflict, match="cannot be released"):
        fixture.service.expire(
            fixture.actor, fixture.ns, fixture.dataset.id, published.id, PublicationRelease()
        )
    assert (
        fixture.service.get(fixture.actor, fixture.ns, fixture.dataset.id, published.id).state
        == "published"
    )


def test_competing_finalizers_return_one_version_and_one_committed_success_event(
    publication_fixture, sessions
):
    fixture = publication_fixture()
    group = prepare(fixture, start(fixture)[0])
    barrier = Barrier(2)

    def finalize():
        barrier.wait(timeout=5)
        return fixture.service.finalize(fixture.actor, fixture.ns, fixture.dataset.id, group.id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: finalize(), range(2)))
    assert first.version_id == second.version_id and first.state == second.state == "published"
    with sessions() as session:
        assert scalar_count(session, DatasetVersionRow, fixture.ns) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(OperationEventRow)
                .where(
                    OperationEventRow.operation_id == group.id,
                    OperationEventRow.event_type == "operation.succeeded",
                )
            )
            == 1
        )


def test_grant_revoked_during_actual_source_verification_cannot_accept_the_batch(
    publication_fixture, sessions, monkeypatch
):
    fixture = publication_fixture(count=1)
    group, _ = start(fixture)
    original = fixture.storage.verify

    def verify_then_revoke(key, expected, **kwargs):
        result = original(key, expected, **kwargs)
        IdentityService(sessions).revoke_grant(fixture.actor.id, fixture.ns)
        return result

    monkeypatch.setattr(fixture.storage, "verify", verify_then_revoke)
    with pytest.raises(Forbidden):
        fixture.runner.prepare(
            fixture.actor,
            fixture.ns,
            fixture.dataset.id,
            group.id,
            PublicationBatch(expected_count=0),
        )
    with sessions() as session:
        row = session.get(PublicationGroupRow, group.id)
        assert row.state == "building" and row.prepared_count == 0
        assert (
            session.get(RetentionReferenceRow, (group.id, fixture.artifact_id)).verified_at is None
        )
        assert scalar_count(session, DatasetVersionRow, fixture.ns) == 0


def test_manifest_work_is_fenced_by_new_claim_and_release(publication_fixture):
    fixture = publication_fixture(count=1)
    group, _ = start(fixture)
    proof = fixture.service.claim_batch(
        fixture.actor, fixture.ns, fixture.dataset.id, group.id, PublicationBatch(expected_count=0)
    )
    for member in proof.members:
        fixture.storage.verify(member.artifact.object_key, member.artifact.digest)
    fixture.service.accept_batch(fixture.actor, fixture.ns, fixture.dataset.id, proof)
    first = fixture.service.claim_manifest(fixture.actor, fixture.ns, fixture.dataset.id, group.id)
    latest = fixture.service.claim_manifest(fixture.actor, fixture.ns, fixture.dataset.id, group.id)
    spool = io.BytesIO()
    digest = write_manifest(
        spool,
        latest.header,
        fixture.service.manifest_members(fixture.actor, fixture.ns, fixture.dataset.id, latest),
        expected_member_count=latest.expected_count,
    )
    with pytest.raises(Conflict, match="stale"):
        fixture.service.allocate_manifest(
            fixture.actor, fixture.ns, fixture.dataset.id, first, digest
        )
    source = fixture.service.allocate_manifest(
        fixture.actor, fixture.ns, fixture.dataset.id, latest, digest
    )
    fixture.storage.write_immutable(source.object_key, spool, source.digest)
    current = fixture.service.claim_manifest(
        fixture.actor, fixture.ns, fixture.dataset.id, group.id
    )
    with pytest.raises(Conflict, match="stale"):
        fixture.service.accept_manifest(
            fixture.actor, fixture.ns, fixture.dataset.id, latest, source
        )
    fixture.service.release(
        fixture.actor, fixture.ns, fixture.dataset.id, group.id, PublicationRelease()
    )
    with pytest.raises(Conflict, match="permit preparation"):
        fixture.service.accept_manifest(
            fixture.actor, fixture.ns, fixture.dataset.id, current, source
        )
