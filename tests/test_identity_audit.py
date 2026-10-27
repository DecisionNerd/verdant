import json
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from verdant.domain import SYSTEM_ACTOR_ID
from verdant.identity import IdentityService
from verdant.operator import main
from verdant.persistence import AuditRow, NamespaceGrantRow, PrincipalRow


def test_identity_audit_is_atomic_scoped_and_immutable(registry, sessions, monkeypatch):
    _, actor, ns = registry
    identities = IdentityService(sessions)
    subject = str(uuid4())
    target = identities.provision(
        "https://audit.test", subject, kind="human", email="private@example.test"
    )
    identities.provision("https://audit.test", subject, kind="human", email="changed@example.test")
    identities.grant(target, ns, "write", actor_id=actor.id)
    identities.revoke_grant(target, ns, actor_id=actor.id)
    identities.set_enabled(target, False, actor_id=actor.id)
    with sessions() as session:
        rows = session.scalars(select(AuditRow).where(AuditRow.aggregate_id == target)).all()
        actions = {row.action for row in rows}
        assert actions == {
            "identity.provisioned",
            "identity.profile_updated",
            "identity.grant_set",
            "identity.grant_revoked",
            "identity.disabled",
        }
        assert not any("@example.test" in str(row.payload) for row in rows)
        assert all(row.actor_id == actor.id for row in rows if row.namespace_id is not None)
        generation = session.get(PrincipalRow, target).session_generation
    audit = identities._audit

    def fail(*args, **kwargs):
        audit(*args, **kwargs)
        raise RuntimeError("injected audit transaction failure")

    monkeypatch.setattr(identities, "_audit", fail)
    with pytest.raises(RuntimeError):
        identities.set_enabled(target, False, actor_id=actor.id)
    with sessions() as session:
        assert session.get(PrincipalRow, target).session_generation == generation
        assert len(
            session.scalars(select(AuditRow).where(AuditRow.aggregate_id == target)).all()
        ) == len(rows)
    with pytest.raises(IntegrityError), sessions.begin() as session:
        session.execute(
            text("UPDATE audit_events SET action='changed' WHERE aggregate_id=:id"), {"id": target}
        )
    with pytest.raises(IntegrityError), sessions.begin() as session:
        session.add(
            AuditRow(
                namespace_id=None,
                actor_id=actor.id,
                aggregate_id=target,
                action="dataset.created",
                payload={},
            )
        )


def test_operator_commands_recover_without_rebinding_identity(registry, sessions, capsys):
    _, operator, ns = registry
    actor = ["--actor", str(operator.id)]
    issuer, subject = "https://operator.test", str(uuid4())
    main(actor + ["provision", issuer, subject, "--kind", "service"])
    principal = json.loads(capsys.readouterr().out)["principal_id"]
    main(actor + ["provision", issuer, subject, "--kind", "service"])
    assert json.loads(capsys.readouterr().out)["principal_id"] == principal
    main(actor + ["grant", principal, str(ns), "write"])
    main(actor + ["disable", principal])
    main(actor + ["enable", principal])
    main(actor + ["revoke-grant", principal, str(ns)])
    main(actor + ["audit", principal])
    events = json.loads(capsys.readouterr().out)
    assert {event["actor_id"] for event in events} == {str(operator.id)}
    assert len(events) == 5
    main(actor + ["cleanup-sessions"])
    main(actor + ["audit", str(SYSTEM_ACTOR_ID)])
    cleanup_events = json.loads(capsys.readouterr().out)
    cleanup = [event for event in cleanup_events if event["action"] == "identity.sessions_cleaned"]
    assert any(event["actor_id"] == str(operator.id) for event in cleanup)
    with sessions() as session:
        from uuid import UUID

        assert session.get(NamespaceGrantRow, (UUID(principal), ns)) is None
        assert session.get(PrincipalRow, UUID(principal)).enabled
