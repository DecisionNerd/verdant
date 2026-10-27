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
