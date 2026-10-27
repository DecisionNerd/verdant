"""Local identities and grants; provider claims never serve as resource permissions."""

from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from verdant.domain import Forbidden, Principal
from verdant.persistence import ExternalIdentityRow, NamespaceGrantRow, PrincipalRow


def current_principal(session: Session, principal_id: UUID) -> Principal:
    row = session.get(PrincipalRow, principal_id)
    if row is None or not row.enabled:
        raise Forbidden("Principal disabled or unavailable")
    grants = session.scalars(
        select(NamespaceGrantRow).where(NamespaceGrantRow.principal_id == principal_id)
    ).all()
    return Principal(
        id=principal_id,
        readable_namespaces=frozenset(g.namespace_id for g in grants if g.access == "read"),
        writable_namespaces=frozenset(g.namespace_id for g in grants if g.access == "write"),
    )


class IdentityService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def provision(self, issuer: str, subject: str, *, kind: str, email: str | None = None) -> UUID:
        """Trusted operator/JIT boundary: exact subject only; caller must validate admission."""
        if not issuer or not subject or len(issuer) > 500 or len(subject) > 255:
            raise ValueError("Invalid external identity")
        if kind not in {"human", "service"}:
            raise ValueError("Invalid principal kind")
        with self.sessions.begin() as session:
            # Serialize first login by the external identity, including the not-yet-existing case.
            # A transaction-scoped advisory lock avoids an orphan principal on concurrent JIT.
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
                {"identity": issuer + "\n" + subject},
            )
            binding = session.scalar(
                select(ExternalIdentityRow).where(
                    ExternalIdentityRow.issuer == issuer,
                    ExternalIdentityRow.subject == subject,
                )
            )
            if binding is not None:
                principal = session.get(PrincipalRow, binding.principal_id)
                if not principal.enabled or principal.kind != kind:
                    raise Forbidden("Principal disabled or incompatible")
                binding.email = email
                result = principal.id
            else:
                principal = PrincipalRow(kind=kind)
                session.add(principal)
                session.flush()
                session.add(
                    ExternalIdentityRow(
                        issuer=issuer,
                        subject=subject,
                        principal_id=principal.id,
                        email=email,
                    )
                )
                result = principal.id
        return result

    def resolve(self, issuer: str, subject: str, *, kind: str | None = None) -> Principal:
        with self.sessions.begin() as session:
            binding = session.scalar(
                select(ExternalIdentityRow).where(
                    ExternalIdentityRow.issuer == issuer,
                    ExternalIdentityRow.subject == subject,
                )
            )
            if binding is None:
                raise Forbidden("Principal not provisioned")
            if kind is not None and session.get(PrincipalRow, binding.principal_id).kind != kind:
                raise Forbidden("Principal kind denied")
            return current_principal(session, binding.principal_id)

    def set_enabled(self, principal_id: UUID, enabled: bool):
        """Trusted operator boundary: disabling invalidates all prior browser sessions."""
        with self.sessions.begin() as session:
            values = {"enabled": enabled}
            if not enabled:
                values["session_generation"] = PrincipalRow.session_generation + 1
            session.execute(
                update(PrincipalRow).where(PrincipalRow.id == principal_id).values(**values)
            )

    def session_generation(self, principal_id: UUID) -> int:
        with self.sessions() as session:
            row = session.get(PrincipalRow, principal_id)
            if row is None or not row.enabled:
                raise Forbidden("Principal disabled or unavailable")
            return row.session_generation

    def grant(self, principal_id: UUID, namespace_id: UUID, access: str):
        """Trusted operator boundary, not exposed as an unauthenticated API."""
        if access not in {"read", "write"}:
            raise ValueError("Invalid namespace access")
        with self.sessions.begin() as session:
            session.execute(
                insert(NamespaceGrantRow)
                .values(
                    principal_id=principal_id,
                    namespace_id=namespace_id,
                    access=access,
                )
                .on_conflict_do_update(
                    index_elements=["principal_id", "namespace_id"],
                    set_={"access": access},
                )
            )
