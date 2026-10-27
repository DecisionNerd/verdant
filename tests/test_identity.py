from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from verdant.contracts import DatasetCreate
from verdant.domain import Forbidden
from verdant.identity import IdentityService
from verdant.persistence import ExternalIdentityRow, NamespaceGrantRow, PrincipalRow


def test_subject_mapping_survives_email_changes_and_collisions(sessions):
    identity = IdentityService(sessions)
    issuer, subject = "https://issuer.test/" + str(uuid4()), str(uuid4())
    original = identity.provision(issuer, subject, kind="human", email="old@example.test")
    renamed = identity.provision(issuer, subject, kind="human", email="new@example.test")
    collision = identity.provision(issuer, str(uuid4()), kind="human", email="new@example.test")
    other_issuer = identity.provision(
        issuer + "other", subject, kind="human", email="new@example.test"
    )
    assert original == renamed
    assert len({original, collision, other_issuer}) == 3
    assert identity.resolve(issuer, subject).id == original
    with pytest.raises(Forbidden):
        identity.provision(issuer, subject, kind="service")


def test_concurrent_first_login_has_one_mapping_and_principal(sessions):
    identity = IdentityService(sessions)
    issuer, subject = "https://issuer.test/" + str(uuid4()), str(uuid4())
    barrier = Barrier(2)

    def provision(_):
        barrier.wait(timeout=10)
        return identity.provision(issuer, subject, kind="human")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(provision, range(2)))
    assert results[0] == results[1]
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ExternalIdentityRow)
                .where(
                    ExternalIdentityRow.issuer == issuer,
                )
            )
            == 1
        )


def test_current_grants_and_disabled_principal_checked_at_use_case_boundary(registry, sessions):
    service, actor, ns = registry
    d = service.create_dataset(
        actor, ns, DatasetCreate(slug="access", name="Access", modality="image")
    )
    with sessions.begin() as session:
        session.execute(
            delete(NamespaceGrantRow).where(
                NamespaceGrantRow.principal_id == actor.id,
            )
        )
    # The caller's old Principal still has write permission in its snapshot.
    # Current database grants remain authoritative.
    assert ns in actor.writable_namespaces
    with pytest.raises(Forbidden):
        service.get_dataset(actor, ns, d.id)
    IdentityService(sessions).grant(actor.id, ns, "write")
    assert service.get_dataset(actor, ns, d.id) == d
    with sessions.begin() as session:
        session.get(PrincipalRow, actor.id).enabled = False
    with pytest.raises(Forbidden):
        service.get_dataset(actor, ns, d.id)
