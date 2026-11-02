"""Trusted native transport fakes exercise domain invariants; browser proof uses real SSO."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from verdant import annotation_contracts as a
from verdant import contracts as c
from verdant.annotations import AnnotationService, normalize
from verdant.domain import Conflict, Forbidden
from verdant.identity import IdentityService
from verdant.label_studio import LabelStudioConflict, LabelStudioSettings, NativeAnnotation
from verdant.persistence import (
    CanonicalAnnotationRow,
    ExternalIdentityRow,
)
from verdant.truth import TruthService, derive_validation, leakage_check

SCHEMA = a.CategoricalSchema(
    labels=["positive", "negative"],
    instruction_revision="v1",
    instructions="Choose the sentiment from the text.",
)
SETTINGS = LabelStudioSettings(
    "https://labels.example.test",
    "https://machine.example.test",
    "example.test/native@sha256:" + "a" * 64,
)


class Native:
    settings = SETTINGS

    def __init__(self):
        self.rows = {}
        self.data = {}
        self.schema = SCHEMA.native_xml()

    def project_schema(self, project):
        return self.schema

    def task_data(self, project, task):
        return self.data[task]

    def task_annotations(self, project, task, digest):
        if hashlib.sha256(self.schema.encode()).hexdigest() != digest:
            raise LabelStudioConflict("schema differs")
        return self.rows.get(task, [])


def annotation(
    id=1,
    native_user=101,
    task=1,
    label="positive",
    assistance="independent",
    cancelled=False,
    modifier=None,
):
    result = (
        []
        if label is None
        else [
            {
                "from_name": "label",
                "to_name": "text",
                "type": "choices",
                "value": {"choices": [label]},
            }
        ]
    )
    if assistance:
        result += [
            {
                "from_name": "assistance",
                "to_name": "text",
                "type": "choices",
                "value": {"choices": [assistance]},
            }
        ]
    return NativeAnnotation(
        id=id,
        task=task,
        completed_by=native_user,
        updated_by=native_user if modifier is None else modifier,
        result=result,
        was_cancelled=cancelled,
    )


@pytest.fixture
def fixture(registry, sessions, principal_factory):
    registry, owner, ns = registry
    editor = principal_factory(ns)
    outsider = principal_factory()
    dataset = registry.create_dataset(
        owner, ns, c.DatasetCreate(slug="truth", name="Truth", modality="text")
    )
    native = Native()
    native.settings = LabelStudioSettings(
        f"https://labels-{ns}.example.test", f"https://machine-{ns}.example.test", SETTINGS.image
    )
    service = AnnotationService(sessions, native)
    cohort = service.admit_cohort(
        ns,
        name="Trusted cohort",
        settings=native.settings,
        issuer="https://test-issuer",
        reason="verified operator fixture",
    )
    with sessions() as session:
        identities = [
            session.scalar(
                select(ExternalIdentityRow).where(ExternalIdentityRow.principal_id == actor.id)
            )
            for actor in (owner, editor)
        ]
        subjects = [row.subject for row in identities]
    for actor, subject, native_id in zip((owner, editor), subjects, (101, 102), strict=True):
        service.bind_contributor(
            cohort,
            issuer="https://test-issuer",
            subject=subject,
            native_user_id=native_id,
            reviewer=actor.id == owner.id,
            reason="verified native operator inventory",
        )
    members = []
    for index in range(5):
        content = f"Example content {index}".encode()
        revision = registry.create_sample(
            owner,
            ns,
            c.SampleCreate(
                source_key=str(index),
                modality="text",
                sha256=hashlib.sha256(content).hexdigest(),
                object_key="qualification/" + str(uuid4()),
                size_bytes=len(content),
                metadata={"group_id": f"g{index}", "answer": "POISON_DO_NOT_EVALUATE"},
            ),
        )
        registry.set_membership(
            owner,
            ns,
            dataset.id,
            revision.sample_id,
            c.MembershipWrite(
                expected_revision=index,
                sample_revision_id=revision.id,
                split="production" if index == 4 else "train",
            ),
        )
        native.data[index + 1] = {"text": content.decode(), "verdant_revision_id": str(revision.id)}
        members.append(revision)
    assignments = []
    for index, member in enumerate(members):
        assignments.append(
            service.create(
                owner,
                ns,
                dataset.id,
                a.AssignmentCreate(
                    request_key=str(uuid4()),
                    expected_revision=5,
                    cohort_id=cohort,
                    sample_id=member.sample_id,
                    sample_revision_id=member.id,
                    native_project_id=1,
                    native_task_id=index + 1,
                    task_schema=SCHEMA,
                    contributors=[owner.id, editor.id],
                ),
            )
        )
    return (
        service,
        native,
        registry,
        owner,
        editor,
        outsider,
        ns,
        dataset,
        cohort,
        members,
        assignments,
    )


def imported(f):
    (
        service,
        native,
        registry,
        owner,
        editor,
        outsider,
        ns,
        dataset,
        cohort,
        members,
        assignments,
    ) = f
    for index, assignment in enumerate(assignments):
        native.rows[index + 1] = [
            annotation(index * 2 + 1, 101, index + 1),
            annotation(index * 2 + 2, 102, index + 1, "negative" if index == 1 else "positive"),
        ]
        service.import_native(
            owner, ns, dataset.id, assignment.id, a.ImportRequest(expected_original_count=0)
        )
    return service.report(
        owner,
        ns,
        dataset.id,
        a.ReportCreate(
            request_key=str(uuid4()), assignment_ids=[assignment.id for assignment in assignments]
        ),
    )


def canonicals(f, report):
    (
        service,
        native,
        registry,
        owner,
        editor,
        outsider,
        ns,
        dataset,
        cohort,
        members,
        assignments,
    ) = f
    return [
        service.adjudicate(
            owner,
            ns,
            dataset.id,
            a.Adjudicate(
                request_key=str(uuid4()),
                report_id=report.id,
                assignment_id=assignment.id,
                expected_canonical_id=None,
                label="positive",
                rationale="Reviewed exact source and both independent originals",
            ),
        )
        for assignment in assignments
    ]


def test_import_idempotency_authoritative_history_cancel_revert_and_auth(fixture, sessions):
    (
        service,
        native,
        registry,
        owner,
        editor,
        outsider,
        ns,
        dataset,
        cohort,
        members,
        assignments,
    ) = fixture
    assignment = assignments[0]
    native.rows[1] = [annotation()]
    first = service.import_native(
        owner, ns, dataset.id, assignment.id, a.ImportRequest(expected_original_count=0)
    )
    assert len(first.originals) == 1 and first.latest[0].payload["assistance"] == "independent"
    assert (
        service.import_native(
            owner, ns, dataset.id, assignment.id, a.ImportRequest(expected_original_count=0)
        )
        == first
    )
    native.rows[1] = [annotation(label="negative")]
    with pytest.raises(Conflict, match="history changed"):
        service.import_native(
            owner, ns, dataset.id, assignment.id, a.ImportRequest(expected_original_count=0)
        )
    service.import_native(
        owner, ns, dataset.id, assignment.id, a.ImportRequest(expected_original_count=1)
    )
    native.rows[1] = [annotation()]
    reverted = service.import_native(
        owner, ns, dataset.id, assignment.id, a.ImportRequest(expected_original_count=2)
    )
    assert [row.payload["label"] for row in reverted.originals] == [
        "positive",
        "negative",
        "positive",
    ]
    assert reverted.originals[0].digest == reverted.originals[2].digest
    native.rows[1] = []
    deleted = service.import_native(
        owner, ns, dataset.id, assignment.id, a.ImportRequest(expected_original_count=3)
    )
    assert deleted.originals[-1].payload["native_deleted"]
    assert deleted.latest[0].payload["state"] == "cancelled"
    assert len(service.detail(owner, ns, dataset.id, assignment.id).originals) == 4
    with pytest.raises(Forbidden):
        service.detail(outsider, ns, dataset.id, assignment.id)
    with pytest.raises(Forbidden):
        service.report(
            outsider,
            ns,
            dataset.id,
            a.ReportCreate(request_key=str(uuid4()), assignment_ids=[assignment.id]),
        )
    IdentityService(sessions).revoke_grant(editor.id, ns)
    native.rows[1] = [annotation(2, 102)]
    with pytest.raises(Forbidden):
        service.import_native(
            owner, ns, dataset.id, assignment.id, a.ImportRequest(expected_original_count=4)
        )
    with sessions.begin() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text("UPDATE annotation_originals SET digest=:digest WHERE id=:id"),
                {"digest": "b" * 64, "id": first.originals[0].id},
            )


def test_provenance_conflicts_schema_and_native_task_content(fixture):
    (
        service,
        native,
        registry,
        owner,
        editor,
        outsider,
        ns,
        dataset,
        cohort,
        members,
        assignments,
    ) = fixture
    native.rows[1] = [annotation(native_user=999)]
    with pytest.raises(Conflict, match="contributor"):
        service.import_native(
            owner, ns, dataset.id, assignments[0].id, a.ImportRequest(expected_original_count=0)
        )
    native.rows[1] = [annotation(), annotation(9, 101)]
    with pytest.raises(Conflict, match="Multiple active"):
        service.import_native(
            owner, ns, dataset.id, assignments[0].id, a.ImportRequest(expected_original_count=0)
        )
    native.rows[1] = [annotation()]
    native.schema = SCHEMA.native_xml() + " "
    with pytest.raises(LabelStudioConflict):
        service.import_native(
            owner, ns, dataset.id, assignments[0].id, a.ImportRequest(expected_original_count=0)
        )
    native.schema = SCHEMA.native_xml()
    native.data[1]["text"] = "mutated"
    with pytest.raises(LabelStudioConflict, match="source revision"):
        service.import_native(
            owner, ns, dataset.id, assignments[0].id, a.ImportRequest(expected_original_count=0)
        )
    with pytest.raises(Conflict):
        service.bind_contributor(
            cohort,
            issuer="https://test-issuer",
            subject="wrong",
            native_user_id=101,
            reason="cannot rebind",
        )


def test_cross_author_modification_cannot_claim_independent_provenance(fixture):
    (
        service,
        native,
        registry,
        owner,
        editor,
        outsider,
        ns,
        dataset,
        cohort,
        members,
        assignments,
    ) = fixture
    native.rows[1] = [annotation(modifier=102), annotation(2, 102)]
    detail = service.import_native(
        owner, ns, dataset.id, assignments[0].id, a.ImportRequest(expected_original_count=0)
    )
    first = detail.originals[0]
    assert first.payload["declared_assistance"] == "independent"
    assert first.payload["assistance"] == "unknown"
    assert first.payload["independence"] == "cross_author_edit"
    assert first.payload["modifier"]["principal_id"] == str(editor.id)
    report = service.report(
        owner,
        ns,
        dataset.id,
        a.ReportCreate(request_key="modifiers", assignment_ids=[assignments[0].id]),
    )
    assert report.payload["pairs"][0]["declared_independent_overlap"] == 0


@pytest.mark.parametrize(
    "result",
    [
        {"from_name": {}, "to_name": "text", "type": "choices", "value": {"choices": ["positive"]}},
        {
            "from_name": "assistance",
            "to_name": "text",
            "type": "choices",
            "value": {"choices": [["independent"]]},
        },
        {
            "from_name": "label",
            "to_name": "text",
            "type": "choices",
            "value": {"choices": ["other"]},
        },
    ],
)
def test_malformed_native_result_is_safe_conflict(result):
    with pytest.raises(Conflict):
        normalize(
            NativeAnnotation(id=1, task=1, completed_by=1, result=[result], was_cancelled=False),
            SCHEMA,
        )


def test_reports_preserve_original_identity_and_statistics_undefined_and_missing(fixture):
    (
        service,
        native,
        registry,
        owner,
        editor,
        outsider,
        ns,
        dataset,
        cohort,
        members,
        assignments,
    ) = fixture
    report = imported(fixture)
    pair = report.payload["pairs"][0]
    assert pair["overlap"] == 5 and pair["agreed"] == 4
    assert pair["observed_agreement"] == 0.8
    assert pair["cohen_kappa"] == 0
    assert 0 <= pair["agreement_95_wilson"][0] < 0.8 < pair["agreement_95_wilson"][1] <= 1
    assert len(report.payload["disagreement_queue"]) == 1
    original_snapshot = report.model_dump(mode="json")
    native.rows[2][1] = annotation(4, 102, 2, "positive")
    service.import_native(
        owner, ns, dataset.id, assignments[1].id, a.ImportRequest(expected_original_count=2)
    )
    constant = service.report(
        owner,
        ns,
        dataset.id,
        a.ReportCreate(
            request_key="constant", assignment_ids=[assignment.id for assignment in assignments]
        ),
    )
    assert constant.payload["pairs"][0]["cohen_kappa"] is None
    assert constant.payload["pairs"][0]["kappa_undefined_reason"] == "constant identical marginals"
    assert (
        service.get_evidence(owner, ns, dataset.id, report.id).model_dump(mode="json")
        == original_snapshot
    )
    native.rows[1] = [annotation(label=None, assistance=None)]
    service.import_native(
        owner, ns, dataset.id, assignments[0].id, a.ImportRequest(expected_original_count=2)
    )
    incomplete = service.report(
        owner,
        ns,
        dataset.id,
        a.ReportCreate(request_key="incomplete", assignment_ids=[assignments[0].id]),
    )
    assert incomplete.payload["counts"]["abstained"] == 1
    assert incomplete.payload["counts"]["missing"] == 1
    assert incomplete.payload["pairs"][0]["agreement_95_wilson"] is None


def test_adjudication_role_stale_originals_stale_canonical_correction_and_immutability(
    fixture, sessions
):
    (
        service,
        native,
        registry,
        owner,
        editor,
        outsider,
        ns,
        dataset,
        cohort,
        members,
        assignments,
    ) = fixture
    report = imported(fixture)
    command = a.Adjudicate(
        request_key="review",
        report_id=report.id,
        assignment_id=assignments[0].id,
        expected_canonical_id=None,
        label="positive",
        rationale="Compared both originals against source",
    )
    with pytest.raises(Forbidden):
        service.adjudicate(editor, ns, dataset.id, command)
    first = service.adjudicate(owner, ns, dataset.id, command)
    assert service.adjudicate(owner, ns, dataset.id, command) == first
    with pytest.raises(Conflict, match="Canonical truth changed"):
        service.adjudicate(
            owner, ns, dataset.id, command.model_copy(update={"request_key": "stale"})
        )
    correction = service.adjudicate(
        owner,
        ns,
        dataset.id,
        command.model_copy(
            update={
                "request_key": "corrected",
                "expected_canonical_id": first.id,
                "label": "negative",
                "rationale": "Version correction after source review",
            }
        ),
    )
    assert correction.payload["annotation"]["payload"]["prior_id"] == str(first.id)
    assert service.get_evidence(owner, ns, dataset.id, first.id, canonical=True) == first
    native.rows[1] = [annotation(label="negative")]
    service.import_native(
        owner, ns, dataset.id, assignments[0].id, a.ImportRequest(expected_original_count=2)
    )
    with pytest.raises(Conflict, match="stale"):
        service.adjudicate(
            owner,
            ns,
            dataset.id,
            command.model_copy(
                update={"request_key": "old-report", "expected_canonical_id": correction.id}
            ),
        )
    with sessions.begin() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text("DELETE FROM canonical_annotations WHERE id=:id"), {"id": first.id}
            )


def test_truth_derivation_exact_pins_leakage_test_reuse_audit_and_old_references(fixture, sessions):
    (
        service,
        native,
        registry,
        owner,
        editor,
        outsider,
        ns,
        dataset,
        cohort,
        members,
        assignments,
    ) = fixture
    reviews = canonicals(fixture, imported(fixture))
    truth = TruthService(sessions)
    requested = [
        a.TruthMember(
            sample_id=member.sample_id,
            partition="production" if index == 4 else "test" if index == 3 else "train",
            canonical_id=None if index == 4 else reviews[index].id,
        )
        for index, member in enumerate(members)
    ]
    command = a.TruthPublish(
        request_key="truth1", expected_revision=5, expected_truth_id=None, members=requested
    )
    first = truth.publish(owner, ns, dataset.id, command)
    assert first.payload["validation_derived"] is True
    assert {m["partition"] for m in first.payload["members"]} == {
        "train",
        "validation",
        "test",
        "production",
    }
    assert truth.publish(owner, ns, dataset.id, command) == first
    old = registry.list_memberships(owner, ns, dataset.id, revision=6)
    train = next(m for m in old.members if m.split == "train")
    assert train.interpretation.annotation_revision.payload["id"] in {
        str(review.id) for review in reviews
    }
    assert (
        train.interpretation.review_evidence.annotation_digest
        == train.interpretation.annotation_revision.sha256
    )
    assert train.interpretation.task_schema_revision.payload == SCHEMA.model_dump()
    # Audit-only advancement preserves the untouched holdout without declaring evaluation reuse.
    audited = requested[-1].model_copy(
        update={
            "canonical_id": reviews[-1].id,
            "audit": a.AuditSelection(
                source_truth_id=first.id,
                stratum="daily-random",
                inclusion_probability=0.25,
                selection_method="seeded sample in declared stratum",
            ),
        }
    )
    second_command = a.TruthPublish(
        request_key="truth2",
        expected_revision=6,
        expected_truth_id=first.id,
        members=[*requested[:-1], audited],
    )
    second = truth.publish(owner, ns, dataset.id, second_command)
    assert second.payload["exploratory_test_reuse"] is False
    assert second.payload["evaluation_use_declaration"] == "not_declared"
    assert [m for m in first.payload["members"] if m["partition"] == "test"] == [
        m for m in second.payload["members"] if m["partition"] == "test"
    ]
    prod = next(
        m for m in second.payload["members"] if m["sample_id"] == str(members[-1].sample_id)
    )
    assert prod["partition"] == "production" and prod["audit"]["inclusion_probability"] == 0.25
    assert prod["audit"]["canonical_id"] == str(reviews[-1].id)
    assert truth.get(owner, ns, dataset.id, first.id) == first
    assert registry.list_memberships(owner, ns, dataset.id, revision=6) == old
    with pytest.raises(Forbidden):
        truth.get(outsider, ns, dataset.id, first.id)
    with pytest.raises(Conflict, match="Truth version changed"):
        truth.publish(
            owner,
            ns,
            dataset.id,
            second_command.model_copy(update={"request_key": "stale", "expected_revision": 7}),
        )
    with pytest.raises(Conflict, match="Fresh confirmation"):
        truth.publish(
            owner,
            ns,
            dataset.id,
            second_command.model_copy(
                update={
                    "request_key": "bad-confirmation",
                    "expected_revision": 7,
                    "expected_truth_id": second.id,
                    "confirmation_of": first.id,
                    "exploratory_test_reuse": True,
                }
            ),
        )
    with sessions.begin() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text("UPDATE truth_versions SET digest=:digest WHERE id=:id"),
                {"digest": "b" * 64, "id": first.id},
            )


def test_derive_preserves_group_and_duplicate_components_and_adverse_boundaries():
    members = [
        {"group": "a", "content_digest": "same", "partition": "train"},
        {"group": "b", "content_digest": "same", "partition": "train"},
        {"group": "c", "content_digest": "other", "partition": "train"},
    ]
    assert derive_validation(members)
    assert members[0]["partition"] == members[1]["partition"]
    leakage_check(members)
    for field in ("group", "content_digest"):
        bad = [
            {"group": "a", "content_digest": "x", "partition": "train"},
            {"group": "b", "content_digest": "y", "partition": "test"},
        ]
        bad[1][field] = bad[0][field]
        with pytest.raises(Conflict, match="leakage"):
            leakage_check(bad)
    with pytest.raises(Conflict, match="two independent"):
        derive_validation([{"group": "one", "content_digest": "x", "partition": "train"}])
    with pytest.raises(Conflict, match="duplicate-connected"):
        derive_validation(
            [
                {"group": "one", "content_digest": "x", "partition": "train"},
                {"group": "two", "content_digest": "x", "partition": "train"},
            ]
        )


def test_concurrent_duplicate_import_has_one_effect(fixture):
    (
        service,
        native,
        registry,
        owner,
        editor,
        outsider,
        ns,
        dataset,
        cohort,
        members,
        assignments,
    ) = fixture
    native.rows[1] = [annotation()]
    barrier = Barrier(2)

    def run():
        barrier.wait()
        return service.import_native(
            owner, ns, dataset.id, assignments[0].id, a.ImportRequest(expected_original_count=0)
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: run(), range(2)))
    assert len(results[0].originals) == len(results[1].originals) == 1
    assert results[0].originals[0].id == results[1].originals[0].id


def test_canonical_head_uses_chain_not_transaction_timestamps(fixture, sessions):
    (
        service,
        native,
        registry,
        owner,
        editor,
        outsider,
        ns,
        dataset,
        cohort,
        members,
        assignments,
    ) = fixture
    report = imported(fixture)
    command = a.Adjudicate(
        request_key="head1",
        report_id=report.id,
        assignment_id=assignments[0].id,
        expected_canonical_id=None,
        label="positive",
        rationale="First review",
    )
    # Begin an older transaction before the first review's transaction. Its correction
    # receives an older now() timestamp despite acquiring the dataset guard later.
    with sessions.begin() as old_transaction:
        old_transaction.execute(text("SELECT now()"))
        first = service.adjudicate(owner, ns, dataset.id, command)
        from sqlalchemy.orm import sessionmaker

        bound = sessionmaker(
            bind=old_transaction.connection(),
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        correction = AnnotationService(bound, native).adjudicate(
            owner,
            ns,
            dataset.id,
            command.model_copy(
                update={
                    "request_key": "head2",
                    "expected_canonical_id": first.id,
                    "label": "negative",
                }
            ),
        )
    with sessions() as session:
        predecessor = session.get(CanonicalAnnotationRow, first.id)
        successor = session.get(CanonicalAnnotationRow, correction.id)
        assert successor.created_at < predecessor.created_at
    assert service.detail(owner, ns, dataset.id, assignments[0].id).canonical_id == correction.id
    member = next(
        m
        for m in TruthService(sessions).workspace(owner, ns, dataset.id)["members"]
        if m["sample_id"] == str(members[0].sample_id)
    )
    assert member["canonical_id"] == str(correction.id)


def test_prior_test_entering_training_requires_explicit_disclosure(fixture, sessions):
    (
        service,
        native,
        registry,
        owner,
        editor,
        outsider,
        ns,
        dataset,
        cohort,
        members,
        assignments,
    ) = fixture
    reviews = canonicals(fixture, imported(fixture))
    truth = TruthService(sessions)
    requested = [
        a.TruthMember(
            sample_id=member.sample_id,
            partition="production" if index == 4 else "test" if index == 3 else "train",
            canonical_id=None if index == 4 else reviews[index].id,
        )
        for index, member in enumerate(members)
    ]
    first = truth.publish(
        owner,
        ns,
        dataset.id,
        a.TruthPublish(
            request_key="initial", expected_revision=5, expected_truth_id=None, members=requested
        ),
    )
    moved = [
        m.model_copy(update={"partition": "train"}) if i == 3 else m
        for i, m in enumerate(requested)
    ]
    command = a.TruthPublish(
        request_key="contamination", expected_revision=6, expected_truth_id=first.id, members=moved
    )
    with pytest.raises(Conflict, match="exploratory"):
        truth.publish(owner, ns, dataset.id, command)
    declared = truth.publish(
        owner, ns, dataset.id, command.model_copy(update={"exploratory_test_reuse": True})
    )
    assert declared.payload["reused_test_samples"] == [str(members[3].sample_id)]
    assert declared.payload["exploratory_test_reuse"] is True
    assert declared.payload["evaluation_use_declaration"] == "not_declared"


@pytest.mark.parametrize("which", ["machine", "browser", "cross_browser", "cross_machine", "swap"])
def test_shared_native_origin_cannot_be_admitted_into_another_namespace(fixture, sessions, which):
    (
        service,
        native,
        registry,
        owner,
        editor,
        outsider,
        ns,
        dataset,
        cohort,
        members,
        assignments,
    ) = fixture
    from verdant.persistence import NamespaceRow

    second_namespace = uuid4()
    with sessions.begin() as session:
        session.add(NamespaceRow(id=second_namespace, name="Must remain isolated"))
    current = native.settings
    fresh_browser = f"https://browser-{uuid4()}.example.test"
    fresh_machine = f"https://machine-{uuid4()}.example.test"
    browser, machine = {
        "machine": (fresh_browser, current.machine_origin),
        "browser": (current.browser_origin, fresh_machine),
        "cross_browser": (current.machine_origin, fresh_machine),
        "cross_machine": (fresh_browser, current.browser_origin),
        "swap": (current.machine_origin, current.browser_origin),
    }[which]
    with pytest.raises(Conflict, match="native instance|origins"):
        service.admit_cohort(
            second_namespace,
            name="Cannot fake a separate cohort",
            settings=LabelStudioSettings(browser, machine, current.image),
            issuer="https://test-issuer",
            reason="Adversarial admission",
        )
    assert (
        service.admit_cohort(
            ns,
            name="Trusted cohort",
            settings=current,
            issuer="https://test-issuer",
            reason="Idempotent repeat",
        )
        == cohort
    )


def test_concurrent_origin_alias_admission_is_globally_serialized(registry, sessions):
    _, owner, ns = registry
    from verdant.persistence import NamespaceRow

    second_namespace = uuid4()
    with sessions.begin() as session:
        session.add(NamespaceRow(id=second_namespace, name="Second namespace"))
    machine = f"https://machine-{uuid4()}.example.test"
    barrier = Barrier(2)

    def run(namespace):
        barrier.wait()
        try:
            return AnnotationService(sessions).admit_cohort(
                namespace,
                name="One actual instance",
                settings=LabelStudioSettings(
                    f"https://browser-{uuid4()}.example.test", machine, SETTINGS.image
                ),
                issuer="https://test-issuer",
                reason="Concurrent admission",
            )
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(run, [ns, second_namespace]))
    assert sum(result == "conflict" for result in results) == 1
    assert sum(isinstance(result, UUID) for result in results) == 1


def test_database_enforces_machine_origin_uniqueness(fixture, sessions):
    (
        service,
        native,
        registry,
        owner,
        editor,
        outsider,
        ns,
        dataset,
        cohort,
        members,
        assignments,
    ) = fixture
    from verdant.persistence import AnnotationCohortRow

    with sessions.begin() as session:
        session.add(
            AnnotationCohortRow(
                namespace_id=ns,
                name="Aliased shared machine",
                browser_origin=f"https://different-{uuid4()}.example.test",
                machine_origin=native.settings.machine_origin,
                image=SETTINGS.image,
                issuer="https://test-issuer",
                reason="Must fail at database boundary",
            )
        )
        with pytest.raises(IntegrityError) as error:
            session.flush()
        assert error.value.orig.diag.constraint_name == "uq_annotation_cohort_machine_origin"


def test_native_extra_fields_are_rejected_before_any_original_is_committed(fixture, sessions):
    (
        service,
        native,
        registry,
        owner,
        editor,
        outsider,
        ns,
        dataset,
        cohort,
        members,
        assignments,
    ) = fixture
    invalid = annotation().model_dump()
    invalid["result"][0]["unexpected"] = "unsupported native field"
    native.rows[1] = [NativeAnnotation.model_validate(invalid)]
    with pytest.raises(Conflict, match="supported contract"):
        service.import_native(
            owner, ns, dataset.id, assignments[0].id, a.ImportRequest(expected_original_count=0)
        )
    detail = service.detail(owner, ns, dataset.id, assignments[0].id)
    assert detail.originals == []
    assert a.AssignmentDetailResponse.model_validate(detail.model_dump(mode="json")).originals == []
    native.rows[1] = [annotation()]
    accepted = service.import_native(
        owner, ns, dataset.id, assignments[0].id, a.ImportRequest(expected_original_count=0)
    )
    assert (
        len(a.AssignmentDetailResponse.model_validate(accepted.model_dump(mode="json")).originals)
        == 1
    )


def test_deleted_native_identity_rotation_remains_within_public_detail_contract(fixture):
    (
        service,
        native,
        registry,
        owner,
        editor,
        outsider,
        ns,
        dataset,
        cohort,
        members,
        assignments,
    ) = fixture
    assignment = assignments[0]
    count = 0
    for native_id in range(1, 102):
        native.rows[1] = [annotation(native_id, 101)]
        detail = service.import_native(
            owner, ns, dataset.id, assignment.id, a.ImportRequest(expected_original_count=count)
        )
        count += 1
        if native_id < 101:
            native.rows[1] = []
            detail = service.import_native(
                owner, ns, dataset.id, assignment.id, a.ImportRequest(expected_original_count=count)
            )
            count += 1
    assert count == len(detail.originals) == 201
    assert len(detail.latest) == 101
    assert sum(row.payload.get("native_deleted", False) for row in detail.latest) == 100
    public = a.AssignmentDetailResponse.model_validate(detail.model_dump(mode="json"))
    assert len(public.latest) == 101
    assert public.model_dump(mode="json", by_alias=True, exclude_unset=True) == detail.model_dump(
        mode="json"
    )


def test_native_skip_is_abstention_while_deleted_original_is_missing(fixture):
    (
        service,
        native,
        registry,
        owner,
        editor,
        outsider,
        ns,
        dataset,
        cohort,
        members,
        assignments,
    ) = fixture
    assignment = assignments[0]
    native.rows[1] = [annotation(cancelled=True, label=None)]
    detail = service.import_native(
        owner, ns, dataset.id, assignment.id, a.ImportRequest(expected_original_count=0)
    )
    assert detail.originals[0].payload["state"] == "cancelled"
    assert detail.missing_contributors == [editor.id]
    report = service.report(
        owner,
        ns,
        dataset.id,
        a.ReportCreate(request_key="native-skip", assignment_ids=[assignment.id]),
    )
    assert report.payload["counts"]["abstained"] == 1
    assert report.payload["counts"]["missing"] == 1
    native.rows[1] = []
    deleted = service.import_native(
        owner, ns, dataset.id, assignment.id, a.ImportRequest(expected_original_count=1)
    )
    assert len(deleted.originals) == 2
    after = service.report(
        owner,
        ns,
        dataset.id,
        a.ReportCreate(request_key="native-deleted", assignment_ids=[assignment.id]),
    )
    assert after.payload["counts"]["missing"] == 2


def test_fresh_confirmation_assigns_new_content_groups_and_retains_old_holdout(fixture, sessions):
    (
        service,
        native,
        registry,
        owner,
        editor,
        outsider,
        ns,
        dataset,
        cohort,
        members,
        assignments,
    ) = fixture
    reviews = canonicals(fixture, imported(fixture))
    truth = TruthService(sessions)
    initial = [
        a.TruthMember(
            sample_id=member.sample_id,
            partition="production" if index == 4 else "test" if index == 3 else "train",
            canonical_id=None if index == 4 else reviews[index].id,
        )
        for index, member in enumerate(members)
    ]
    first = truth.publish(
        owner,
        ns,
        dataset.id,
        a.TruthPublish(
            request_key="before-confirmation",
            expected_revision=5,
            expected_truth_id=None,
            members=initial,
        ),
    )
    content = b"New untouched confirmation source"
    fresh = registry.create_sample(
        owner,
        ns,
        c.SampleCreate(
            source_key="fresh-confirmation",
            modality="text",
            sha256=hashlib.sha256(content).hexdigest(),
            object_key="qualification/" + str(uuid4()),
            size_bytes=len(content),
            metadata={"group_id": "fresh-confirmation-group"},
        ),
    )
    registry.set_membership(
        owner,
        ns,
        dataset.id,
        fresh.sample_id,
        c.MembershipWrite(expected_revision=6, sample_revision_id=fresh.id, split="production"),
    )
    native.data[6] = {"text": content.decode(), "verdant_revision_id": str(fresh.id)}
    assigned = service.create(
        owner,
        ns,
        dataset.id,
        a.AssignmentCreate(
            request_key="fresh-task",
            expected_revision=7,
            cohort_id=cohort,
            sample_id=fresh.sample_id,
            sample_revision_id=fresh.id,
            native_project_id=1,
            native_task_id=6,
            task_schema=SCHEMA,
            contributors=[owner.id, editor.id],
        ),
    )
    native.rows[6] = [annotation(11, 101, 6), annotation(12, 102, 6)]
    service.import_native(
        owner, ns, dataset.id, assigned.id, a.ImportRequest(expected_original_count=0)
    )
    report = service.report(
        owner,
        ns,
        dataset.id,
        a.ReportCreate(request_key="fresh-report", assignment_ids=[assigned.id]),
    )
    canonical = service.adjudicate(
        owner,
        ns,
        dataset.id,
        a.Adjudicate(
            request_key="fresh-review",
            report_id=report.id,
            assignment_id=assigned.id,
            expected_canonical_id=None,
            label="positive",
            rationale="Reviewed new confirmation source",
        ),
    )
    proposed = [
        a.TruthMember(
            sample_id=UUID(m["sample_id"]),
            partition="production" if m["partition"] == "test" else m["partition"],
            canonical_id=UUID(m["canonical_id"])
            if m["canonical_id"] and m["partition"] != "test"
            else None,
        )
        for m in first.payload["members"]
    ]
    proposed.append(
        a.TruthMember(sample_id=fresh.sample_id, partition="test", canonical_id=canonical.id)
    )
    confirmed = truth.publish(
        owner,
        ns,
        dataset.id,
        a.TruthPublish(
            request_key="fresh-confirmation",
            expected_revision=7,
            expected_truth_id=first.id,
            confirmation_of=first.id,
            members=proposed,
        ),
    )
    assert confirmed.payload["confirmation_of"] == {"id": str(first.id), "digest": first.digest}
    assert confirmed.payload["exploratory_test_reuse"] is False
    assert [m["sample_id"] for m in confirmed.payload["members"] if m["partition"] == "test"] == [
        str(fresh.sample_id)
    ]
    assert truth.get(owner, ns, dataset.id, first.id) == first
