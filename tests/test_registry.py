from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from verdant import contracts as c
from verdant.api import create_app, verified_principal
from verdant.domain import Conflict, Forbidden, NotFound, Principal
from verdant.persistence import AuditRow, MembershipRow, NamespaceRow, SampleRevisionRow


def dataset(service, actor, namespace, slug="corpus"):
    return service.create_dataset(
        actor, namespace, c.DatasetCreate(slug=slug, name="Corpus", modality="document")
    )


def sample(service, actor, namespace, key="page-one"):
    return service.create_sample(
        actor,
        namespace,
        c.SampleCreate(
            source_key=key,
            modality="image",
            sha256="a" * 64,
            object_key="sha256/" + "a" * 64,
            size_bytes=17,
            metadata={"page": 1},
        ),
    )


def write(revision, expected, split="train"):
    return c.MembershipWrite(
        expected_revision=expected, sample_revision_id=revision.id, split=split
    )


def test_stable_sample_and_immutable_metadata_revisions(registry):
    service, actor, ns = registry
    first = sample(service, actor, ns)
    second = service.revise_sample(
        actor,
        ns,
        first.sample_id,
        c.SampleRevise(
            expected_revision=1,
            sha256=first.sha256,
            object_key=first.object_key,
            size_bytes=17,
            metadata={"page": 2},
        ),
    )
    third = service.revise_sample(
        actor,
        ns,
        first.sample_id,
        c.SampleRevise(
            expected_revision=2,
            sha256="b" * 64,
            object_key="sha256/" + "b" * 64,
            size_bytes=18,
            metadata={"page": 2},
        ),
    )
    assert first.sample_id == second.sample_id == third.sample_id
    assert len({first.id, second.id, third.id}) == 3
    assert [first.revision, second.revision, third.revision] == [1, 2, 3]
    assert first.sha256 == second.sha256 != third.sha256
    assert service.get_sample_revision(actor, ns, first.sample_id, first.id) == first
    with pytest.raises(Conflict, match="Sample revision changed"):
        service.revise_sample(
            actor,
            ns,
            first.sample_id,
            c.SampleRevise(
                expected_revision=1,
                sha256=first.sha256,
                object_key=first.object_key,
                size_bytes=17,
            ),
        )


def test_reuse_membership_history_removal_and_pagination(registry):
    service, actor, ns = registry
    a, b = dataset(service, actor, ns), dataset(service, actor, ns, "other")
    first, second = sample(service, actor, ns), sample(service, actor, ns, "page-two")
    service.set_membership(actor, ns, a.id, first.sample_id, write(first, 0))
    service.set_membership(actor, ns, b.id, first.sample_id, write(first, 0, "test"))
    service.set_membership(actor, ns, a.id, first.sample_id, write(first, 1, "validation"))
    no_op = service.set_membership(actor, ns, a.id, first.sample_id, write(first, 2, "validation"))
    assert no_op.revision == 2
    service.set_membership(actor, ns, a.id, second.sample_id, write(second, 2))
    page = service.list_memberships(actor, ns, a.id, limit=1)
    assert page.revision == 3 and len(page.members) == 1 and page.next_after
    last = service.list_memberships(actor, ns, a.id, revision=3, after=page.next_after, limit=1)
    assert len(last.members) == 1 and last.next_after is None
    assert page.members[0].sample_id != last.members[0].sample_id
    service.remove_membership(actor, ns, a.id, first.sample_id, 3)
    assert service.list_memberships(actor, ns, a.id, revision=0).members == []
    original = service.list_memberships(actor, ns, a.id, revision=1)
    assert [(m.sample_revision_id, m.split) for m in original.members] == [(first.id, "train")]
    assert service.list_memberships(actor, ns, b.id).members[0].split == "test"
    assert [m.sample_id for m in service.list_memberships(actor, ns, a.id).members] == [
        second.sample_id
    ]
    with pytest.raises(NotFound):
        service.list_memberships(actor, ns, a.id, revision=5)


def test_concurrent_stale_draft_write_has_one_winner(registry, sessions):
    service, actor, ns = registry
    d = dataset(service, actor, ns)
    r = sample(service, actor, ns)
    barrier = Barrier(2)

    def attempt(split):
        barrier.wait(timeout=10)
        try:
            return service.set_membership(actor, ns, d.id, r.sample_id, write(r, 0, split))
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["train", "test"]))
    assert results.count("conflict") == 1
    assert [x.revision for x in results if x != "conflict"] == [1]
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditRow)
                .where(AuditRow.namespace_id == ns, AuditRow.action == "draft.membership_set")
            )
            == 1
        )


def test_command_and_audit_rollback_together(registry, sessions, monkeypatch):
    service, actor, ns = registry
    d = dataset(service, actor, ns)
    r = sample(service, actor, ns)
    audit = service._audit

    def fail(*args):
        audit(*args)
        raise RuntimeError("injected before commit")

    monkeypatch.setattr(service, "_audit", fail)
    with pytest.raises(RuntimeError, match="injected"):
        service.set_membership(actor, ns, d.id, r.sample_id, write(r, 0))
    assert service.list_memberships(actor, ns, d.id).revision == 0
    assert service.list_memberships(actor, ns, d.id).members == []
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditRow)
                .where(AuditRow.namespace_id == ns, AuditRow.action == "draft.membership_set")
            )
            == 0
        )


def test_namespace_and_sample_reference_constraints(registry, sessions):
    service, actor, ns = registry
    d = dataset(service, actor, ns)
    r = sample(service, actor, ns)
    other_ns = uuid4()
    with sessions.begin() as session:
        session.add(NamespaceRow(id=other_ns, name="elsewhere"))
    other_actor = Principal(uuid4(), writable_namespaces=frozenset({other_ns}))
    other = sample(service, other_actor, other_ns)
    same_namespace_other_sample = sample(service, actor, ns, "different")
    for bad_revision in (other, same_namespace_other_sample):
        with pytest.raises(NotFound):
            service.set_membership(actor, ns, d.id, r.sample_id, write(bad_revision, 0))
        with pytest.raises(IntegrityError) as caught, sessions.begin() as session:
            session.add(
                MembershipRow(
                    namespace_id=ns,
                    draft_id=d.draft_id,
                    sample_id=r.sample_id,
                    sample_revision_id=bad_revision.id,
                    valid_from=1,
                    split="train",
                )
            )
        assert caught.value.orig.sqlstate == "23503"


def test_database_protects_history_and_intervals(registry, sessions):
    service, actor, ns = registry
    d = dataset(service, actor, ns)
    r = sample(service, actor, ns)
    service.set_membership(actor, ns, d.id, r.sample_id, write(r, 0))
    service.set_membership(actor, ns, d.id, r.sample_id, write(r, 1, "test"))
    statements = [
        ("UPDATE sample_revisions SET metadata='{}'::jsonb WHERE id=:id", r.id),
        ("DELETE FROM sample_revisions WHERE id=:id", r.id),
        ("UPDATE draft_memberships SET split='wrong' WHERE draft_id=:id", d.draft_id),
        ("DELETE FROM draft_memberships WHERE draft_id=:id", d.draft_id),
        ("UPDATE audit_events SET action='changed' WHERE namespace_id=:id", ns),
    ]
    for statement, value in statements:
        with pytest.raises(IntegrityError) as caught, sessions.begin() as session:
            session.execute(text(statement), {"id": value})
        assert caught.value.orig.sqlstate == "23514"
    with pytest.raises(IntegrityError) as caught, sessions.begin() as session:
        session.add(
            MembershipRow(
                namespace_id=ns,
                draft_id=d.draft_id,
                sample_id=r.sample_id,
                sample_revision_id=r.id,
                valid_from=1,
                valid_to=4,
                split="train",
            )
        )
    assert caught.value.orig.sqlstate in {"23505", "23P01"}
    with pytest.raises(IntegrityError), sessions.begin() as session:
        session.add(
            SampleRevisionRow(
                namespace_id=ns,
                sample_id=r.sample_id,
                revision=2,
                sha256="invalid",
                object_key="k",
                size_bytes=-1,
                metadata_json={},
            )
        )


def test_authorization_parity_and_api_fail_closed(registry, sessions):
    service, actor, ns = registry
    d = dataset(service, actor, ns)
    app = create_app(sessions)
    path = f"/api/v1/namespaces/{ns}/datasets/{d.id}"
    with TestClient(app) as client:
        assert (
            client.get(
                path, headers={"X-Namespace": str(ns), "X-Principal": str(actor.id)}
            ).status_code
            == 401
        )
        app.dependency_overrides[verified_principal] = lambda: actor
        assert client.get(path).json() == service.get_dataset(actor, ns, d.id).model_dump(
            mode="json"
        )
        assert (
            client.post(
                f"/api/v1/namespaces/{ns}/datasets",
                json={"slug": "bad slug", "name": "x", "modality": "image"},
            ).status_code
            == 422
        )
        denied = Principal(uuid4())
        app.dependency_overrides[verified_principal] = lambda: denied
        existing = client.get(path)
        absent = client.get(f"/api/v1/namespaces/{ns}/datasets/{uuid4()}")
        assert existing.status_code == absent.status_code == 403
        assert existing.json() == absent.json() == {"detail": "Namespace access denied"}
        assert (
            client.get(f"/api/v1/namespaces/{ns}/datasets/{d.id}/draft/members").status_code == 403
        )
        with pytest.raises(Forbidden):
            service.get_dataset(denied, ns, d.id)
        reader = Principal(uuid4(), readable_namespaces=frozenset({ns}))
        app.dependency_overrides[verified_principal] = lambda: reader
        assert client.get(path).status_code == 200
        assert (
            client.post(
                f"/api/v1/namespaces/{ns}/datasets",
                json={"slug": "new", "name": "new", "modality": "image"},
            ).status_code
            == 403
        )


def test_bootstrap_and_repeat_upgrade_preserve_state(registry, sessions):
    service, actor, ns = registry
    d = dataset(service, actor, ns)
    r = sample(service, actor, ns)
    service.set_membership(actor, ns, d.id, r.sample_id, write(r, 0))
    command.upgrade(Config("alembic.ini"), "head")
    with TestClient(create_app(sessions)) as client:
        health = client.get("/health/ready")
        assert health.status_code == 200
        assert health.json()["schema_revision"] == c.SCHEMA_REVISION
        assert health.json()["identity"] == "unqualified"
    assert service.get_dataset(actor, ns, d.id) == d
    assert service.get_sample_revision(actor, ns, r.sample_id, r.id) == r
    assert service.list_memberships(actor, ns, d.id).revision == 1


def test_duplicate_identity_conflicts_are_scoped(registry, sessions):
    service, actor, ns = registry
    dataset(service, actor, ns)
    sample(service, actor, ns)
    with pytest.raises(Conflict):
        dataset(service, actor, ns)
    with pytest.raises(Conflict):
        sample(service, actor, ns)
    other_ns = uuid4()
    with sessions.begin() as session:
        session.add(NamespaceRow(id=other_ns, name="elsewhere"))
    other = Principal(uuid4(), writable_namespaces=frozenset({other_ns}))
    assert dataset(service, other, other_ns).namespace_id == other_ns
    assert sample(service, other, other_ns).namespace_id == other_ns


def test_http_lifecycle_and_historical_paging(registry, sessions):
    _, actor, ns = registry
    app = create_app(sessions)
    app.dependency_overrides[verified_principal] = lambda: actor
    with TestClient(app) as client:
        base = f"/api/v1/namespaces/{ns}"
        response = client.post(
            base + "/datasets", json={"slug": "http", "name": "HTTP", "modality": "image"}
        )
        assert response.status_code == 201
        d = response.json()
        members = base + f"/datasets/{d['id']}/draft/members"
        created = []
        for index in range(3):
            response = client.post(
                base + "/samples",
                json={
                    "source_key": f"page-{index}",
                    "modality": "image",
                    "sha256": "a" * 64,
                    "object_key": f"pages/{index}",
                    "size_bytes": 17,
                },
            )
            assert response.status_code == 201
            r = response.json()
            created.append(r)
            response = client.put(
                members + f"/{r['sample_id']}",
                json={
                    "expected_revision": index,
                    "sample_revision_id": r["id"],
                    "split": "train",
                },
            )
            assert response.status_code == 200
            assert response.json()["revision"] == index + 1
        first = client.get(members, params={"limit": 1}).json()
        remaining = sorted(created, key=lambda r: r["sample_id"])[1:]
        removed = remaining[0]
        response = client.delete(
            members + f"/{removed['sample_id']}", params={"expected_revision": 3}
        )
        assert response.status_code == 200 and response.json()["revision"] == 4
        assert (
            client.delete(
                members + f"/{removed['sample_id']}", params={"expected_revision": 3}
            ).status_code
            == 409
        )
        second = client.get(
            members,
            params={
                "revision": first["revision"],
                "after": first["next_after"],
                "limit": 5,
            },
        ).json()
        assert second["revision"] == 3
        assert [m["sample_id"] for m in second["members"]] == [r["sample_id"] for r in remaining]
        r = created[0]
        response = client.post(
            base + f"/samples/{r['sample_id']}/revisions",
            json={
                "expected_revision": 1,
                "sha256": r["sha256"],
                "object_key": r["object_key"],
                "size_bytes": 17,
                "metadata": {"corrected": True},
            },
        )
        assert response.status_code == 201
        assert response.json()["sample_id"] == r["sample_id"]
        assert response.json()["revision"] == 2
        assert client.get(base + f"/samples/{r['sample_id']}/revisions/{r['id']}").json() == r
        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["schema_revision"] == c.SCHEMA_REVISION
