from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from verdant.api import create_app, verified_principal
from verdant.contracts import DatasetCreate, MembershipWrite, SampleCreate, SampleRevise
from verdant.domain import Conflict, Forbidden, NotFound
from verdant.identity import IdentityService
from verdant.persistence import AuditRow, QualityPolicyRow, QualityReportRow
from verdant.quality import QualityDefinition
from verdant.quality_service import PolicyCreate, QualityService, ValidationBatch, ValidationStart
from verdant.versioning import CandidateCapture, CandidatePrepare, VersioningService


@pytest.fixture
def quality(registry, sessions):
    registry_service, actor, ns = registry
    dataset = registry_service.create_dataset(
        actor, ns, DatasetCreate(name="Quality", slug="quality", modality="text")
    )
    return QualityService(sessions), registry_service, actor, ns, dataset


def author(service, actor, ns, dataset, *, name="standard", expected=0, minimum=1, request=None):
    return service.create_policy(
        actor,
        ns,
        dataset.id,
        PolicyCreate(
            request_key=request or str(uuid4()),
            name=name,
            expected_revision=expected,
            definition=QualityDefinition.model_validate(
                {
                    "rules": [
                        {"id": "size", "kind": "minimum_samples", "minimum": minimum},
                        {"id": "label", "kind": "required_metadata", "key": "label"},
                    ]
                }
            ),
        ),
    )


def captured(registry, sessions, actor, ns, dataset, *, count=3, sealed=True):
    samples = []
    for index in range(count):
        sample = registry.create_sample(
            actor,
            ns,
            SampleCreate(
                source_key=f"sample-{uuid4()}",
                modality="text",
                object_key=f"private/{index}",
                sha256="a" * 64,
                size_bytes=1,
                metadata={"label": "cat"} if index else {},
            ),
        )
        registry.set_membership(
            actor,
            ns,
            dataset.id,
            sample.sample_id,
            MembershipWrite(expected_revision=index, sample_revision_id=sample.id, split="train"),
        )
        samples.append(sample)
    versioning = VersioningService(sessions)
    candidate = versioning.capture(
        actor, ns, dataset.id, CandidateCapture(request_key=str(uuid4()), expected_revision=count)
    )
    if sealed:
        candidate = versioning.prepare(
            actor, ns, dataset.id, candidate.id, CandidatePrepare(expected_count=0, limit=100)
        )
    return candidate, samples


def test_policy_revisions_idempotency_conflicts_and_sql_immutability(quality, sessions):
    service, registry, actor, ns, dataset = quality
    first = author(service, actor, ns, dataset, request="first")
    assert author(service, actor, ns, dataset, request="first") == first
    with pytest.raises(Conflict, match="different inputs"):
        author(service, actor, ns, dataset, request="first", minimum=2)
    with pytest.raises(Conflict, match="revision 1"):
        author(service, actor, ns, dataset, minimum=2)
    second = author(service, actor, ns, dataset, expected=1, minimum=2)
    assert second.id != first.id and second.revision == 2
    assert service.get_policy(actor, ns, dataset.id, first.id) == first
    page = service.list_policies(actor, ns, dataset.id, limit=1)
    tail = service.list_policies(actor, ns, dataset.id, after=page.next_after, limit=1)
    assert {page.items[0].id, tail.items[0].id} == {first.id, second.id}
    for sql in [
        "UPDATE quality_policies SET revision=3 WHERE id=:id",
        "DELETE FROM quality_policies WHERE id=:id",
    ]:
        with pytest.raises(IntegrityError), sessions.begin() as session:
            session.execute(text(sql), {"id": first.id})


def test_validation_resume_retries_exact_source_and_reevaluation_append(quality, sessions):
    service, registry, actor, ns, dataset = quality
    policy = author(service, actor, ns, dataset)
    candidate, samples = captured(registry, sessions, actor, ns, dataset)
    command = ValidationStart(request_key="validation", policy_id=policy.id)
    report = service.start(actor, ns, dataset.id, candidate.id, command)
    assert report.required_rules_pass is None
    assert all(result.status == "pending" for result in report.results)
    assert service.start(actor, ns, dataset.id, candidate.id, command) == report
    partial = service.advance(
        actor, ns, dataset.id, report.id, ValidationBatch(expected_count=0, limit=1)
    )
    assert partial.processed_count == 1 and partial.state == "running"
    assert partial.required_rules_pass is None
    # Fresh service instance simulates process restart; no in-memory evaluator state is required.
    resumed = QualityService(sessions)
    assert (
        resumed.advance(
            actor, ns, dataset.id, report.id, ValidationBatch(expected_count=0, limit=1)
        )
        == partial
    )
    with pytest.raises(Conflict, match="ahead"):
        resumed.advance(actor, ns, dataset.id, report.id, ValidationBatch(expected_count=2))
    missing = samples[0]
    changed = registry.revise_sample(
        actor,
        ns,
        missing.sample_id,
        SampleRevise(
            expected_revision=1,
            sha256=missing.sha256,
            object_key=missing.object_key,
            size_bytes=1,
            metadata={"label": "now present"},
        ),
    )
    registry.set_membership(
        actor,
        ns,
        dataset.id,
        changed.sample_id,
        MembershipWrite(expected_revision=3, sample_revision_id=changed.id, split="test"),
    )
    final = resumed.advance(actor, ns, dataset.id, report.id, ValidationBatch(expected_count=1))
    assert final.state == "complete" and final.processed_count == 3
    assert not final.required_rules_pass
    assert final.generation_id == candidate.generation_id and final.source_revision == 3
    failed = next(item for item in final.results if item.rule_id == "label")
    assert failed.failures == 1
    assert failed.offending_samples[0].sample_revision_id == missing.id
    another = resumed.start(
        actor, ns, dataset.id, candidate.id, command.model_copy(update={"request_key": "again"})
    )
    assert another.id != final.id
    assert resumed.get_report(actor, ns, dataset.id, final.id) == final
    with sessions() as session:
        actions = session.scalars(
            select(AuditRow.action).where(AuditRow.aggregate_id == final.id)
        ).all()
    assert actions.count("quality.validation_advanced") == 2
    assert actions.count("quality.validation_started") == 1


def test_empty_generation_completes_with_failed_and_unknown_required_gates(quality, sessions):
    service, registry, actor, ns, dataset = quality
    policy = author(service, actor, ns, dataset)
    candidate, _ = captured(registry, sessions, actor, ns, dataset, count=0)
    report = service.start(
        actor,
        ns,
        dataset.id,
        candidate.id,
        ValidationStart(request_key="empty", policy_id=policy.id),
    )
    final = service.advance(actor, ns, dataset.id, report.id, ValidationBatch(expected_count=0))
    assert final.state == "complete" and not final.required_rules_pass
    assert [item.status for item in final.results] == ["failed", "unknown"]


def test_unsealed_candidate_and_cross_dataset_policy_cannot_validate(quality, sessions):
    service, registry, actor, ns, dataset = quality
    policy = author(service, actor, ns, dataset)
    candidate, _ = captured(registry, sessions, actor, ns, dataset, sealed=False)
    with pytest.raises(Conflict, match="preparation"):
        service.start(
            actor,
            ns,
            dataset.id,
            candidate.id,
            ValidationStart(request_key="unsealed", policy_id=policy.id),
        )
    other = registry.create_dataset(
        actor, ns, DatasetCreate(name="Other", slug="other", modality="text")
    )
    other_candidate, _ = captured(registry, sessions, actor, ns, other, count=0)
    with pytest.raises(NotFound, match="Policy"):
        service.start(
            actor,
            ns,
            other.id,
            other_candidate.id,
            ValidationStart(request_key="cross", policy_id=policy.id),
        )
    with pytest.raises(NotFound, match="Candidate"):
        service.start(
            actor,
            ns,
            other.id,
            candidate.id,
            ValidationStart(request_key="cross-candidate", policy_id=policy.id),
        )


def test_current_access_checked_before_reports_counts_and_progress(
    quality, sessions, principal_factory
):
    service, registry, actor, ns, dataset = quality
    policy = author(service, actor, ns, dataset)
    candidate, _ = captured(registry, sessions, actor, ns, dataset)
    report = service.start(
        actor,
        ns,
        dataset.id,
        candidate.id,
        ValidationStart(request_key="access", policy_id=policy.id),
    )
    denied = principal_factory()
    readonly = principal_factory(ns, "read")
    assert service.get_report(readonly, ns, dataset.id, report.id).id == report.id
    for operation in [
        lambda: service.get_report(denied, ns, dataset.id, report.id),
        lambda: service.list_reports(denied, ns, dataset.id, candidate.id),
        lambda: service.list_policies(denied, ns, dataset.id),
        lambda: service.advance(
            readonly, ns, dataset.id, report.id, ValidationBatch(expected_count=0)
        ),
    ]:
        with pytest.raises(Forbidden):
            operation()
    IdentityService(sessions).revoke_grant(actor.id, ns)
    with pytest.raises(Forbidden):
        service.advance(actor, ns, dataset.id, report.id, ValidationBatch(expected_count=0))
    assert service.get_report(readonly, ns, dataset.id, report.id).processed_count == 0


def test_completed_report_immutable_and_direct_insert_cannot_forge_complete(quality, sessions):
    service, registry, actor, ns, dataset = quality
    policy = author(service, actor, ns, dataset)
    candidate, _ = captured(registry, sessions, actor, ns, dataset, count=0)
    report = service.start(
        actor,
        ns,
        dataset.id,
        candidate.id,
        ValidationStart(request_key="immutable", policy_id=policy.id),
    )
    complete = service.advance(actor, ns, dataset.id, report.id, ValidationBatch(expected_count=0))
    for statement in [
        "UPDATE quality_reports SET results='[]'::jsonb WHERE id=:id",
        "DELETE FROM quality_reports WHERE id=:id",
    ]:
        with pytest.raises(IntegrityError), sessions.begin() as session:
            session.execute(text(statement), {"id": report.id})
    with sessions() as session:
        row = session.get(QualityReportRow, report.id)
        values = {
            column.name: getattr(row, column.name) for column in QualityReportRow.__table__.columns
        }
    values.update(id=uuid4(), request_key="forged")
    with pytest.raises(IntegrityError), sessions.begin() as session:
        session.add(QualityReportRow(**values))
    assert service.get_report(actor, ns, dataset.id, report.id) == complete


def test_competing_retries_do_not_duplicate_policy_report_or_batch(quality, sessions):
    service, registry, actor, ns, dataset = quality
    barrier = Barrier(2)

    def create(_):
        barrier.wait(timeout=5)
        return author(service, actor, ns, dataset, request="race")

    with ThreadPoolExecutor(max_workers=2) as pool:
        policies = list(pool.map(create, range(2)))
    assert policies[0] == policies[1]
    candidate, _ = captured(registry, sessions, actor, ns, dataset)

    def start(_):
        barrier.wait(timeout=5)
        return service.start(
            actor,
            ns,
            dataset.id,
            candidate.id,
            ValidationStart(request_key="race", policy_id=policies[0].id),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(start, range(2)))
    assert reports[0].id == reports[1].id

    def advance(_):
        barrier.wait(timeout=5)
        return service.advance(
            actor, ns, dataset.id, reports[0].id, ValidationBatch(expected_count=0, limit=1)
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(advance, range(2)))
    assert results[0] == results[1] and results[0].processed_count == 1
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(QualityPolicyRow)
                .where(QualityPolicyRow.dataset_id == dataset.id)
            )
            == 1
        )


def test_rest_routes_use_shared_authorized_report_commands(quality, sessions):
    service, registry, actor, ns, dataset = quality
    policy = author(service, actor, ns, dataset)
    candidate, _ = captured(registry, sessions, actor, ns, dataset)
    app = create_app(sessions)
    app.dependency_overrides[verified_principal] = lambda: actor
    base = f"/api/v1/namespaces/{ns}/datasets/{dataset.id}"
    with TestClient(app) as client:
        started = client.post(
            base + f"/candidates/{candidate.id}/quality/reports",
            json={"request_key": "rest", "policy_id": str(policy.id)},
        )
        assert started.status_code == 202
        report_id = started.json()["id"]
        complete = client.post(
            base + f"/quality/reports/{report_id}/evaluate", json={"expected_count": 0}
        )
        assert complete.status_code == 200 and complete.json()["state"] == "complete"
        assert complete.json() == client.get(base + f"/quality/reports/{report_id}").json()
        assert client.get(base + "/quality/policies?limit=0").status_code == 422
        assert (
            client.post(
                base + f"/quality/reports/{report_id}/evaluate",
                json={"expected_count": 0, "limit": 501},
            ).status_code
            == 422
        )
