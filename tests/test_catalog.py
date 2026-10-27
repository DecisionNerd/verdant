from uuid import uuid4

import pytest
from sqlalchemy import delete

from verdant.domain import Forbidden
from verdant.identity import IdentityService
from verdant.persistence import NamespaceGrantRow, NamespaceRow


def test_namespace_pages_filter_current_grants_before_pagination(registry, sessions):
    service, actor, original = registry
    second, hidden = uuid4(), uuid4()
    with sessions.begin() as session:
        session.add_all(
            [NamespaceRow(id=second, name="Second"), NamespaceRow(id=hidden, name="Private")]
        )
    identity = IdentityService(sessions)
    identity.grant(actor.id, second, "read")
    first = service.list_namespaces(actor, limit=1)
    assert len(first.items) == 1
    assert first.next_after == first.items[0].id
    last = service.list_namespaces(actor, after=first.next_after, limit=1)
    assert last.next_after is None
    found = {n.id: n.access for n in first.items + last.items}
    assert found == {original: "write", second: "read"}
    with sessions.begin() as session:
        session.execute(delete(NamespaceGrantRow).where(NamespaceGrantRow.principal_id == actor.id))
    assert service.list_namespaces(actor).items == []
    identity.set_enabled(actor.id, False)
    with pytest.raises(Forbidden):
        service.list_namespaces(actor)


def test_namespace_page_size_is_bounded(registry):
    service, actor, _ = registry
    for invalid in (0, 101):
        with pytest.raises(ValueError):
            service.list_namespaces(actor, limit=invalid)


def test_catalog_filters_counts_and_pages_are_namespace_scoped(registry, principal_factory):
    from verdant.contracts import DatasetCreate

    service, actor, ns = registry
    expected = []
    for i in range(5):
        expected.append(
            service.create_dataset(
                actor,
                ns,
                DatasetCreate(
                    slug=f"page-{i}", name=f"Document {i}", modality="image" if i < 3 else "text"
                ),
            )
        )
    first = service.list_datasets(actor, ns, search="Document", modality="image", limit=2)
    assert first.total == 3
    assert len(first.items) == 2
    second = service.list_datasets(
        actor, ns, search="Document", modality="image", after=first.next_after, limit=2
    )
    assert second.total == 3
    assert second.next_after is None
    assert {d.id for d in first.items + second.items} == {d.id for d in expected[:3]}
    assert service.list_datasets(actor, ns, search="%").total == 0
    assert service.list_datasets(actor, ns, owner_id=uuid4()).items == []
    denied = principal_factory()
    with pytest.raises(Forbidden):
        service.list_datasets(denied, ns)


def test_metadata_updates_preserve_identity_and_reject_stale_writes(registry, sessions):
    from sqlalchemy import select

    from verdant.contracts import DatasetCreate, DatasetUpdate
    from verdant.domain import Conflict
    from verdant.persistence import AuditRow

    service, actor, ns = registry
    created = service.create_dataset(
        actor, ns, DatasetCreate(slug="edit", name="Before", modality="image")
    )
    command = DatasetUpdate(slug="renamed", name="After", modality="text", expected_revision=0)
    changed = service.update_dataset(actor, ns, created.id, command)
    assert changed.revision == 1
    assert changed.name == "After"
    assert (changed.id, changed.draft_id, changed.owner_id) == (
        created.id,
        created.draft_id,
        created.owner_id,
    )
    with pytest.raises(Conflict):
        service.update_dataset(actor, ns, created.id, command)
    assert service.get_dataset(actor, ns, created.id) == changed
    with sessions() as session:
        events = session.scalars(
            select(AuditRow).where(
                AuditRow.aggregate_id == created.id, AuditRow.action == "dataset.updated"
            )
        ).all()
        assert len(events) == 1
        assert events[0].payload["revision"] == 1


def test_concurrent_metadata_edits_have_one_winner(registry):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from verdant.contracts import DatasetCreate, DatasetUpdate
    from verdant.domain import Conflict

    service, actor, ns = registry
    dataset = service.create_dataset(
        actor, ns, DatasetCreate(slug="race", name="Original", modality="image")
    )
    barrier = Barrier(2)

    def save(name):
        barrier.wait(timeout=5)
        try:
            return service.update_dataset(
                actor,
                ns,
                dataset.id,
                DatasetUpdate(slug="race", name=name, modality="image", expected_revision=0),
            ).name
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["First", "Second"]))
    assert results.count("conflict") == 1
    winner = next(name for name in results if name != "conflict")
    stored = service.get_dataset(actor, ns, dataset.id)
    assert stored.name == winner
    assert stored.revision == 1


def test_sample_discovery_omits_private_keys_and_uses_latest_revision(registry, principal_factory):
    from verdant.contracts import SampleCreate, SampleRevise

    service, actor, ns = registry
    first = service.create_sample(
        actor,
        ns,
        SampleCreate(
            source_key="/private/source",
            modality="image",
            sha256="a" * 64,
            object_key="private/secret",
            size_bytes=1,
        ),
    )
    latest = service.revise_sample(
        actor,
        ns,
        first.sample_id,
        SampleRevise(expected_revision=1, sha256="b" * 64, object_key="private/new", size_bytes=2),
    )
    page = service.list_samples(actor, ns, limit=1)
    assert len(page.items) == 1
    assert page.items[0].revision_id == latest.id
    assert page.items[0].revision == 2
    assert page.next_after is None
    assert "private" not in page.model_dump_json()
    inspected = service.inspect_sample_revision(actor, ns, first.sample_id, first.id)
    assert inspected.revision == 1
    assert inspected.sha256 == "a" * 64
    assert "private" not in inspected.model_dump_json()
    with pytest.raises(Forbidden):
        service.inspect_sample_revision(principal_factory(), ns, first.sample_id, first.id)
    with pytest.raises(Forbidden):
        service.list_samples(principal_factory(), ns)


def test_catalog_large_pages_have_no_gaps_or_duplicates(registry, sessions):
    from verdant.persistence import DatasetRow, DraftRow

    service, actor, ns = registry
    ids = [uuid4() for _ in range(1001)]
    with sessions.begin() as session:
        session.add_all(
            [
                DatasetRow(
                    id=key,
                    namespace_id=ns,
                    owner_id=actor.id,
                    slug=f"large-{i}",
                    name=f"Large fixture {i}",
                    modality="image",
                )
                for i, key in enumerate(ids)
            ]
        )
        session.flush()
        session.add_all([DraftRow(namespace_id=ns, dataset_id=key) for key in ids])
    after, found, pages = None, [], 0
    while True:
        page = service.list_datasets(actor, ns, search="Large fixture", after=after, limit=100)
        assert page.total == 1001
        assert 1 <= len(page.items) <= 100
        found.extend(item.id for item in page.items)
        pages += 1
        after = page.next_after
        if after is None:
            break
    assert pages == 11
    assert found == sorted(ids)


def test_namespace_deep_link_resolves_without_loading_catalog_pages(registry, principal_factory):
    service, actor, ns = registry
    current = service.get_namespace(actor, ns)
    assert current.id == ns
    assert current.access == "write"
    with pytest.raises(Forbidden):
        service.get_namespace(principal_factory(), ns)
