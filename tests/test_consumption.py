import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from test_publication import prepare, start, while_blocked
from test_publication import publication_fixture as publication_fixture

from verdant.api import create_app, verified_principal
from verdant.collection import ArtifactCollector
from verdant.consumption import (
    PREVIEW_BYTES,
    ConsumptionService,
    DatasetAvailability,
    VersionAvailability,
    _Prefix,
)
from verdant.contracts import DatasetUpdate, MembershipWrite, SampleCreate, SampleRevise
from verdant.domain import Conflict, Forbidden, NotFound
from verdant.identity import IdentityService
from verdant.operations import OperationService, VerificationRequest
from verdant.persistence import (
    ArtifactLocationRow,
    ArtifactRow,
    AuditRow,
    DatasetRow,
    DatasetVersionRow,
    SampleArtifactRow,
)
from verdant.publication import PublicationStart
from verdant.quality_service import PolicyCreate, ValidationBatch, ValidationStart
from verdant.storage import ObjectCorrupt, ObjectDigest, ObjectMissing
from verdant.versioning import CandidateCapture, CandidatePrepare, VersioningService


@pytest.fixture
def consumed(publication_fixture, sessions):
    def create(count=2):
        fixture = publication_fixture(count)
        group = prepare(fixture, start(fixture)[0])
        result = fixture.service.finalize(fixture.actor, fixture.ns, fixture.dataset.id, group.id)
        reader = ConsumptionService(sessions, fixture.storage)
        version = reader.get_version(
            fixture.actor, fixture.ns, fixture.dataset.id, result.version_id
        )
        return fixture, reader, version

    return create


def read_args(fixture, version):
    return fixture.actor, fixture.ns, fixture.dataset.id, version.id


def dataset_command(reader, fixture, action, revision):
    return reader.dataset_availability(
        fixture.actor,
        fixture.ns,
        fixture.dataset.id,
        DatasetAvailability(expected_revision=revision, action=action),
    )


def version_command(reader, fixture, version, action):
    return reader.version_availability(
        *read_args(fixture, version),
        VersionAvailability(expected_revision=version.availability_revision, action=action),
    )


def publish_current(fixture, sessions):
    actor, ns, dataset = fixture.actor, fixture.ns, fixture.dataset.id
    revision = fixture.registry.list_memberships(actor, ns, dataset).revision
    versioning = VersioningService(sessions)
    candidate = versioning.capture(
        actor,
        ns,
        dataset,
        CandidateCapture(request_key=str(uuid4()), expected_revision=revision),
    )
    candidate = versioning.prepare(
        actor,
        ns,
        dataset,
        candidate.id,
        CandidatePrepare(expected_count=0, limit=100),
    )
    report = fixture.quality.start(
        actor,
        ns,
        dataset,
        candidate.id,
        ValidationStart(request_key=str(uuid4()), policy_id=fixture.policy.id),
    )
    report = fixture.quality.advance(
        actor,
        ns,
        dataset,
        report.id,
        ValidationBatch(expected_count=0, limit=100),
    )
    group = fixture.service.start(
        actor,
        ns,
        dataset,
        candidate.id,
        PublicationStart(request_key=str(uuid4()), label="v-" + uuid4().hex, report_id=report.id),
    )
    group = prepare(fixture, group)
    return fixture.service.finalize(actor, ns, dataset, group.id).version_id


def add_source(fixture, sessions, body, *, modality="text"):
    key = fixture.source_key + "-extra-" + uuid4().hex
    digest = ObjectDigest(hashlib.sha256(body).hexdigest(), len(body))
    fixture.storage.write_immutable(key, io.BytesIO(body), digest)
    sample = fixture.registry.create_sample(
        fixture.actor,
        fixture.ns,
        SampleCreate(
            source_key=str(uuid4()),
            modality=modality,
            object_key=key,
            sha256=digest.sha256,
            size_bytes=digest.size_bytes,
            metadata={"label": "cat"},
        ),
    )
    with sessions.begin() as session:
        artifact = ArtifactRow(
            namespace_id=fixture.ns,
            sha256=digest.sha256,
            size_bytes=digest.size_bytes,
            verified_at=func.clock_timestamp(),
        )
        session.add(artifact)
        session.flush()
        session.add(
            ArtifactLocationRow(
                namespace_id=fixture.ns,
                artifact_id=artifact.id,
                store_id="primary",
                bucket=fixture.storage.settings.bucket,
                object_key=key,
            )
        )
        session.flush()
        session.add(
            SampleArtifactRow(
                namespace_id=fixture.ns,
                sample_id=sample.sample_id,
                revision_id=sample.id,
                artifact_id=artifact.id,
            )
        )
    draft = fixture.registry.list_memberships(fixture.actor, fixture.ns, fixture.dataset.id)
    fixture.registry.set_membership(
        fixture.actor,
        fixture.ns,
        fixture.dataset.id,
        sample.sample_id,
        MembershipWrite(
            expected_revision=draft.revision, sample_revision_id=sample.id, split="test"
        ),
    )
    return sample


def test_exact_manifest_members_and_partial_lineage_are_retained(consumed):
    fixture, reader, version = consumed()
    args = read_args(fixture, version)
    descriptor = reader.manifest(*args)
    assert descriptor.sha256 == version.manifest_digest
    assert descriptor.model_dump(by_alias=True)["schema"] == "verdant.manifest.v1"
    assert "X-Amz" not in descriptor.model_dump_json()
    output = io.BytesIO()
    digest = reader.stream_manifest(*args, output)
    assert hashlib.sha256(output.getvalue()).hexdigest() == digest.sha256 == version.manifest_digest
    assert len(output.getvalue()) == version.manifest_size
    first = reader.members(*args, limit=1)
    second = reader.members(*args, after=first.next_after, limit=1)
    assert first.total == second.total == 2
    assert {item.sample_id for item in first.items + second.items} == {
        sample.sample_id for sample in fixture.samples
    }
    assert first.lineage_status == "partial"
    statuses = {entry.kind: entry.status for entry in first.items[0].lineage}
    assert statuses["annotation_revision"] == "absent"
    assert statuses["quality_report"] == statuses["quality_policy"] == "present"
    assert "object_key" not in first.model_dump_json()
    assert first.quality_report.id == fixture.report.id
    assert first.quality_policy.id == fixture.policy.id


def test_old_content_survives_metadata_draft_and_availability_changes(consumed):
    fixture, reader, version = consumed()
    args = read_args(fixture, version)
    before = io.BytesIO()
    reader.stream_manifest(*args, before)
    old = fixture.samples[0]
    changed = fixture.registry.revise_sample(
        fixture.actor,
        fixture.ns,
        old.sample_id,
        SampleRevise(
            expected_revision=1,
            sha256=old.sha256,
            object_key=old.object_key,
            size_bytes=old.size_bytes,
            metadata={"label": "changed"},
        ),
    )
    fixture.registry.set_membership(
        fixture.actor,
        fixture.ns,
        fixture.dataset.id,
        old.sample_id,
        MembershipWrite(expected_revision=2, sample_revision_id=changed.id, split="test"),
    )
    deprecated = version_command(reader, fixture, version, "deprecate")
    archived = version_command(reader, fixture, deprecated, "archive")
    assert archived.prior_availability == "deprecated"
    assert reader.list_versions(fixture.actor, fixture.ns, fixture.dataset.id).total == 0
    assert (
        reader.list_versions(
            fixture.actor,
            fixture.ns,
            fixture.dataset.id,
            include_archived=True,
        ).total
        == 1
    )
    dataset_command(reader, fixture, "archive", 0)
    assert fixture.registry.list_datasets(fixture.actor, fixture.ns).total == 0
    assert (
        fixture.registry.list_datasets(
            fixture.actor,
            fixture.ns,
            include_archived=True,
        ).total
        == 1
    )
    assert (
        fixture.registry.get_dataset(
            fixture.actor,
            fixture.ns,
            fixture.dataset.id,
        ).availability
        == "archived"
    )
    assert reader.get_version(*args).availability == "archived"
    page = reader.members(*args)
    frozen = next(item for item in page.items if item.sample_id == old.sample_id)
    assert frozen.sample_revision_id == old.id and frozen.metadata["label"] == "cat"
    after = io.BytesIO()
    reader.stream_manifest(*args, after)
    assert after.getvalue() == before.getvalue()
    assert reader.preview(*args, old.sample_id).text == fixture.body.decode()
    restored = version_command(reader, fixture, archived, "unarchive")
    assert restored.availability == "deprecated"
    dataset_command(reader, fixture, "unarchive", 1)
    assert fixture.registry.list_datasets(fixture.actor, fixture.ns).total == 1
    assert reader.list_versions(fixture.actor, fixture.ns, fixture.dataset.id).total == 1


def test_compare_and_swap_audit_and_sql_guards_preserve_published_content(consumed, sessions):
    fixture, reader, version = consumed()
    archived = version_command(reader, fixture, version, "archive")
    with pytest.raises(Conflict, match="availability changed"):
        version_command(reader, fixture, version, "activate")
    with pytest.raises(Conflict, match="Unarchive"):
        version_command(reader, fixture, archived, "activate")
    for statement in (
        "UPDATE dataset_versions SET label='different' WHERE id=:id",
        "UPDATE dataset_versions SET manifest_digest=repeat('0',64) WHERE id=:id",
        "UPDATE dataset_versions SET availability='active' WHERE id=:id",
        "DELETE FROM dataset_versions WHERE id=:id",
    ):
        with pytest.raises(IntegrityError), sessions.begin() as session:
            session.execute(text(statement), {"id": version.id})
    changed = dataset_command(reader, fixture, "archive", 0)
    with pytest.raises(Conflict, match="metadata changed"):
        fixture.registry.update_dataset(
            fixture.actor,
            fixture.ns,
            fixture.dataset.id,
            DatasetUpdate(
                expected_revision=0, name="Changed", slug=fixture.dataset.slug, modality="text"
            ),
        )
    assert changed.revision == 1
    with sessions() as session:
        actions = session.scalars(
            select(AuditRow.action).where(AuditRow.namespace_id == fixture.ns)
        ).all()
        row = session.get(DatasetVersionRow, version.id)
        assert row.manifest_digest == version.manifest_digest and row.label == version.label
        assert actions.count("version.availability_changed") == 1
        assert actions.count("dataset.availability_changed") == 1


def test_archiving_pauses_new_admission_but_preserves_existing_checkpoints(consumed, sessions):
    fixture, reader, version = consumed()
    actor, ns, dataset = fixture.actor, fixture.ns, fixture.dataset.id
    versioning = VersioningService(sessions)
    capture = CandidateCapture(request_key=str(uuid4()), expected_revision=2)
    candidate = versioning.capture(actor, ns, dataset, capture)
    running = fixture.quality.start(
        actor,
        ns,
        dataset,
        fixture.candidate.id,
        ValidationStart(request_key=str(uuid4()), policy_id=fixture.policy.id),
    )
    group = start(fixture)[0]
    dataset_command(reader, fixture, "archive", 0)
    with pytest.raises(Conflict, match="Archived"):
        versioning.capture(
            actor, ns, dataset, capture.model_copy(update={"request_key": str(uuid4())})
        )
    assert versioning.capture(actor, ns, dataset, capture).id == candidate.id
    with pytest.raises(Conflict, match="Archived"):
        fixture.quality.start(
            actor,
            ns,
            dataset,
            fixture.candidate.id,
            ValidationStart(request_key=str(uuid4()), policy_id=fixture.policy.id),
        )
    with pytest.raises(Conflict, match="Archived"):
        fixture.quality.create_policy(
            actor,
            ns,
            dataset,
            PolicyCreate(
                request_key=str(uuid4()),
                name=fixture.policy.name,
                expected_revision=1,
                definition=fixture.policy.definition,
            ),
        )
    prepared = versioning.prepare(
        actor, ns, dataset, candidate.id, CandidatePrepare(expected_count=0)
    )
    assert prepared.state == "sealed"
    assert (
        fixture.quality.advance(
            actor,
            ns,
            dataset,
            running.id,
            ValidationBatch(expected_count=0),
        ).state
        == "complete"
    )
    group = prepare(fixture, group)
    with pytest.raises(Conflict, match="Archived"):
        fixture.service.finalize(actor, ns, dataset, group.id)
    # Namespace-wide source registration/verification does not mutate an archived draft.
    assert (
        OperationService(sessions)
        .admit_verification(
            actor,
            ns,
            VerificationRequest(
                request_key=str(uuid4()),
                sample_id=fixture.samples[0].sample_id,
                revision_id=fixture.samples[0].id,
            ),
        )
        .status
        == "queued"
    )
    dataset_command(reader, fixture, "unarchive", 1)
    assert fixture.service.finalize(actor, ns, dataset, group.id).state == "published"
    assert (
        reader.get_version(*read_args(fixture, version)).manifest_digest == version.manifest_digest
    )


@pytest.mark.parametrize(
    "operation", ["metadata", "membership", "remove", "candidate", "policy", "validation"]
)
def test_archive_winning_dataset_lock_blocks_new_mutation(consumed, sessions, operation):
    fixture, reader, version = consumed()
    actor, ns, dataset = fixture.actor, fixture.ns, fixture.dataset.id
    sample = fixture.samples[0]
    actions = {
        "metadata": lambda: fixture.registry.update_dataset(
            actor,
            ns,
            dataset,
            DatasetUpdate(
                expected_revision=0,
                name="Changed",
                slug=fixture.dataset.slug,
                modality="text",
            ),
        ),
        "membership": lambda: fixture.registry.set_membership(
            actor,
            ns,
            dataset,
            sample.sample_id,
            MembershipWrite(expected_revision=2, sample_revision_id=sample.id, split="test"),
        ),
        "remove": lambda: fixture.registry.remove_membership(
            actor, ns, dataset, sample.sample_id, 2
        ),
        "candidate": lambda: VersioningService(sessions).capture(
            actor, ns, dataset, CandidateCapture(request_key=str(uuid4()), expected_revision=2)
        ),
        "policy": lambda: fixture.quality.create_policy(
            actor,
            ns,
            dataset,
            PolicyCreate(
                request_key=str(uuid4()),
                name=fixture.policy.name,
                expected_revision=1,
                definition=fixture.policy.definition,
            ),
        ),
        "validation": lambda: fixture.quality.start(
            actor,
            ns,
            dataset,
            fixture.candidate.id,
            ValidationStart(request_key=str(uuid4()), policy_id=fixture.policy.id),
        ),
    }

    def archive(holding, row):
        row.availability, row.revision = "archived", row.revision + 1

    with pytest.raises(Conflict):
        while_blocked(sessions, DatasetRow, dataset, actions[operation], archive)
    assert fixture.registry.get_dataset(actor, ns, dataset).availability == "archived"
    assert (
        reader.get_version(*read_args(fixture, version)).manifest_digest == version.manifest_digest
    )


@pytest.mark.parametrize("output", ["preview", "download", "stream"])
def test_authorization_precedes_counts_members_and_bytes_and_is_rechecked_after_io(
    consumed,
    principal_factory,
    sessions,
    monkeypatch,
    output,
):
    fixture, reader, version = consumed()
    denied, readonly = principal_factory(), principal_factory(fixture.ns, "read")
    target = (fixture.ns, fixture.dataset.id, version.id)
    assert reader.get_version(readonly, *target).id == version.id
    for action in (
        lambda: reader.list_versions(denied, fixture.ns, fixture.dataset.id),
        lambda: reader.members(denied, *target),
        lambda: reader.manifest(denied, *target),
        lambda: reader.preview(denied, *target, fixture.samples[0].sample_id),
        lambda: reader.version_availability(
            readonly, *target, VersionAvailability(expected_revision=0, action="archive")
        ),
        lambda: reader.dataset_availability(
            readonly,
            fixture.ns,
            fixture.dataset.id,
            DatasetAvailability(expected_revision=0, action="archive"),
        ),
    ):
        with pytest.raises(Forbidden):
            action()
    original = fixture.storage.verify
    calls = []

    def revoked(*args, **kwargs):
        result = original(*args, **kwargs)
        IdentityService(sessions).revoke_grant(readonly.id, fixture.ns)
        calls.append(True)
        return result

    monkeypatch.setattr(fixture.storage, "verify", revoked)
    signed = []
    monkeypatch.setattr(fixture.storage, "sign_read", lambda key: signed.append(key))
    destination = io.BytesIO()
    with pytest.raises(Forbidden):
        if output == "preview":
            reader.preview(readonly, *target, fixture.samples[0].sample_id)
        elif output == "download":
            reader.download(readonly, *target, fixture.samples[0].sample_id)
        else:
            reader.stream_manifest(readonly, *target, destination)
    assert calls
    assert signed == [] and destination.getvalue() == b""


def test_competing_version_availability_commands_preserve_one_cas_winner(consumed, sessions):
    fixture, reader, version = consumed()
    barrier = Barrier(2)

    def run(action):
        barrier.wait(timeout=5)
        try:
            return version_command(reader, fixture, version, action)
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(run, ["archive", "deprecate"]))
    assert sum(item is not None for item in outcomes) == 1
    current = reader.get_version(*read_args(fixture, version))
    assert current.availability_revision == 1
    assert current.manifest_digest == version.manifest_digest
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditRow)
                .where(
                    AuditRow.aggregate_id == version.id,
                    AuditRow.action == "version.availability_changed",
                )
            )
            == 1
        )


def test_sample_outside_version_is_not_resolved(consumed):
    fixture, reader, version = consumed()
    outside = fixture.registry.create_sample(
        fixture.actor,
        fixture.ns,
        SampleCreate(
            source_key=str(uuid4()),
            modality="text",
            object_key="unrelated/private",
            sha256="0" * 64,
            size_bytes=1,
        ),
    )
    with pytest.raises(NotFound, match="not a member"):
        reader.preview(*read_args(fixture, version), outside.sample_id)


def test_missing_and_corrupt_bytes_are_explicit_and_never_return_preview(consumed):
    fixture, reader, version = consumed()
    args = (*read_args(fixture, version), fixture.samples[0].sample_id)
    fixture.storage.delete_one(fixture.source_key)
    with pytest.raises(ObjectMissing):
        reader.preview(*args)
    fixture.storage.client.put_object(
        Bucket=fixture.storage.settings.bucket,
        Key=fixture.source_key,
        Body=b"X" * len(fixture.body),
    )
    with pytest.raises(ObjectCorrupt):
        reader.preview(*args)
    assert (
        reader.get_version(*read_args(fixture, version)).manifest_digest == version.manifest_digest
    )


def test_verified_relocation_keeps_snapshot_identity_and_retention(consumed, sessions):
    fixture, reader, version = consumed()
    args = read_args(fixture, version)
    with sessions() as session:
        old = session.scalar(
            select(ArtifactLocationRow).where(
                ArtifactLocationRow.artifact_id == version.manifest_artifact_id,
            )
        )
        original_key, original_id = old.object_key, old.id
    original = io.BytesIO()
    reader.stream_manifest(*args, original)
    new_key = original_key + "-relocated"
    digest = ObjectDigest(version.manifest_digest, version.manifest_size)
    fixture.storage.write_immutable(new_key, io.BytesIO(original.getvalue()), digest)
    fixture.storage.verify(new_key, digest)
    with sessions.begin() as session:
        session.add(
            ArtifactLocationRow(
                id=UUID(int=original_id.int + 1),
                namespace_id=fixture.ns,
                artifact_id=version.manifest_artifact_id,
                store_id="primary",
                bucket=fixture.storage.settings.bucket,
                object_key=new_key,
            )
        )
    fixture.storage.delete_one(original_key)
    restored = io.BytesIO()
    assert reader.stream_manifest(*args, restored) == digest
    assert restored.getvalue() == original.getvalue()
    # Force the earlier placement to stream corrupt bytes before trying the verified copy.
    fixture.storage.client.put_object(
        Bucket=fixture.storage.settings.bucket,
        Key=original_key,
        Body=b"X" * version.manifest_size,
    )
    recovered = io.BytesIO()
    reader.stream_manifest(*args, recovered)
    assert recovered.getvalue() == original.getvalue()
    assert reader.manifest(*args).artifact_id == version.manifest_artifact_id
    with pytest.raises(Conflict, match="protected"):
        ArtifactCollector(sessions, fixture.storage).claim(
            fixture.actor,
            fixture.ns,
            version.manifest_artifact_id,
        )
    with sessions() as session:
        assert session.get(ArtifactLocationRow, original_id).object_key == original_key
        assert session.get(DatasetVersionRow, version.id).manifest_digest == version.manifest_digest


def test_utf8_preview_is_bounded_and_binary_is_explicit(consumed, sessions):
    fixture, reader, _ = consumed()
    body = ("🙂" * 5000).encode()
    sample = add_source(fixture, sessions, body)
    version_id = publish_current(fixture, sessions)
    preview = reader.preview(
        fixture.actor, fixture.ns, fixture.dataset.id, version_id, sample.sample_id
    )
    assert preview.kind == "text" and preview.truncated
    assert len(preview.text.encode()) <= PREVIEW_BYTES
    assert preview.sha256 == hashlib.sha256(body).hexdigest()
    binary = add_source(fixture, sessions, b"\x89PNG\r\n\x1a\n", modality="image")
    binary_version = publish_current(fixture, sessions)
    preview = reader.preview(
        fixture.actor,
        fixture.ns,
        fixture.dataset.id,
        binary_version,
        binary.sample_id,
    )
    assert preview.kind == "binary" and preview.text is None
    assert "unsupported" in preview.warning
    bounded = _Prefix()
    for _ in range(100):
        assert bounded.write(b"x" * 100000) == 100000
    assert len(bounded.data) == PREVIEW_BYTES


def test_rest_download_redirect_hides_signed_credentials_from_descriptors(consumed):
    fixture, reader, version = consumed()
    app = create_app(reader.sessions, artifact_storage=fixture.storage)
    app.dependency_overrides[verified_principal] = lambda: fixture.actor
    base = f"/api/v1/namespaces/{fixture.ns}/datasets/{fixture.dataset.id}"
    with TestClient(app) as client:
        versions = client.get(base + "/versions").json()
        assert versions["total"] == 1
        descriptor = client.get(base + f"/versions/{version.id}/manifest").json()
        assert descriptor["schema"] == "verdant.manifest.v1"
        assert "X-Amz" not in str(descriptor)
        download = client.get(descriptor["download_path"], follow_redirects=False)
        assert download.status_code == 307
        assert download.headers["cache-control"] == "no-store"
        assert download.headers["referrer-policy"] == "no-referrer"
        assert "X-Amz-Signature=" in download.headers["location"]
        path = base + f"/versions/{version.id}/samples/{fixture.samples[0].sample_id}"
        preview = client.get(path + "/preview")
        assert preview.json()["text"] == fixture.body.decode()
        assert preview.headers["cache-control"] == "no-store"
        assert client.get(path + "/download", follow_redirects=False).status_code == 307
        fixture.storage.delete_one(fixture.source_key)
        failure = client.get(path + "/preview")
        assert failure.status_code == 409 and "missing" in failure.json()["detail"]
        assert fixture.source_key not in str(failure.json())
        archive = client.post(
            base + "/availability", json={"expected_revision": 0, "action": "archive"}
        )
        assert archive.status_code == 200 and archive.json()["availability"] == "archived"
