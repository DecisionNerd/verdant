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
