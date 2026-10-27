"""Transport- and persistence-independent authorization and invariant failures."""

from dataclasses import dataclass
from uuid import UUID


class DomainError(Exception):
    """A safe, intentional application failure."""


class Forbidden(DomainError):
    pass


class NotFound(DomainError):
    pass


class Conflict(DomainError):
    pass


@dataclass(frozen=True)
class Principal:
    """Current verified grants supplied by a trusted caller, never parsed from request headers."""

    id: UUID
    readable_namespaces: frozenset[UUID] = frozenset()
    writable_namespaces: frozenset[UUID] = frozenset()
    transport: str = "managed"

    def require(self, namespace_id: UUID, *, write: bool = False) -> None:
        allowed = self.writable_namespaces
        if not write:
            allowed = allowed | self.readable_namespaces
        if namespace_id not in allowed:
            raise Forbidden("Namespace access denied")
