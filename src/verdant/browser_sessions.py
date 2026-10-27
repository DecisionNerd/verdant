"""PostgreSQL store for starsessions and atomic, browser-bound OIDC attempts."""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starsessions import SessionStore

from verdant.domain import SYSTEM_ACTOR_ID
from verdant.persistence import AuditRow, BrowserSessionRow, LoginAttemptRow


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class DatabaseSessionStore(SessionStore):
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def _read(self, session_id: str) -> bytes:
        if not re.fullmatch(r"[0-9a-f]{32}", session_id):
            return b"{}"
        with self.sessions() as session:
            row = session.get(BrowserSessionRow, digest(session_id))
            if row is None or row.revoked or row.expires_at <= datetime.now(UTC):
                return b"{}"
            return row.data

    async def read(self, session_id: str, lifetime: int) -> bytes:
        return await run_in_threadpool(self._read, session_id)

    def _write(self, session_id: str, data: bytes, ttl: int) -> str:
        if not re.fullmatch(r"[0-9a-f]{32}", session_id) or len(data) > 65536:
            raise ValueError("Invalid session storage input")
        now = datetime.now(UTC)
        with self.sessions.begin() as session:
            session.execute(
                insert(BrowserSessionRow)
                .values(
                    token_hash=digest(session_id),
                    data=data,
                    expires_at=now + timedelta(seconds=max(0, ttl)),
                    revoked=False,
                )
                .on_conflict_do_update(
                    index_elements=["token_hash"],
                    set_={"data": data},
                    # Retain the absolute deadline.
                    # A stale request cannot resurrect logout.
                    where=(BrowserSessionRow.revoked.is_(False))
                    & (BrowserSessionRow.expires_at > now),
                )
            )
        return session_id

    async def write(self, session_id: str, data: bytes, lifetime: int, ttl: int) -> str:
        return await run_in_threadpool(self._write, session_id, data, ttl)

    def _remove(self, session_id: str):
        with self.sessions.begin() as session:
            # Keep the tombstone until the original expiry so concurrent saves cannot revive it.
            session.execute(
                update(BrowserSessionRow)
                .where(
                    BrowserSessionRow.token_hash == digest(session_id),
                )
                .values(revoked=True, data=b"{}")
            )

    async def remove(self, session_id: str) -> None:
        await run_in_threadpool(self._remove, session_id)

    def start_attempt(self, state: str, session_id: str):
        with self.sessions.begin() as session:
            session.add(
                LoginAttemptRow(
                    state_hash=digest(state),
                    session_hash=digest(session_id),
                    expires_at=datetime.now(UTC) + timedelta(minutes=5),
                )
            )

    def consume_attempt(self, state: str, session_id: str) -> bool:
        if not state or len(state) > 1024:
            return False
        with self.sessions.begin() as session:
            consumed = session.execute(
                update(LoginAttemptRow)
                .where(
                    LoginAttemptRow.state_hash == digest(state),
                    LoginAttemptRow.session_hash == digest(session_id),
                    LoginAttemptRow.consumed.is_(False),
                    LoginAttemptRow.expires_at > datetime.now(UTC),
                )
                .values(consumed=True)
                .returning(LoginAttemptRow.state_hash)
            ).scalar_one_or_none()
        # Commit before the remote code exchange. Failed exchanges require a fresh login.
        return consumed is not None

    def cleanup_expired(self, *, actor_id: UUID = SYSTEM_ACTOR_ID) -> None:
        from sqlalchemy import delete

        with self.sessions.begin() as session:
            now = datetime.now(UTC)
            sessions_deleted = session.execute(
                delete(BrowserSessionRow).where(BrowserSessionRow.expires_at <= now)
            ).rowcount
            attempts_deleted = session.execute(
                delete(LoginAttemptRow).where(LoginAttemptRow.expires_at <= now)
            ).rowcount
            session.add(
                AuditRow(
                    namespace_id=None,
                    actor_id=actor_id,
                    aggregate_id=SYSTEM_ACTOR_ID,
                    action="identity.sessions_cleaned",
                    schema_version=1,
                    payload={
                        "sessions_deleted": sessions_deleted,
                        "attempts_deleted": attempts_deleted,
                        "transport": "operator",
                        "source": "trusted-process" if actor_id == SYSTEM_ACTOR_ID else "principal",
                    },
                )
            )

    def finalize_attempt(self, session_id: str) -> bool:
        """Fence completion against logout, expiry or a superseding login during exchange."""
        with self.sessions.begin() as session:
            accepted = session.execute(
                update(BrowserSessionRow)
                .where(
                    BrowserSessionRow.token_hash == digest(session_id),
                    BrowserSessionRow.revoked.is_(False),
                    BrowserSessionRow.expires_at > datetime.now(UTC),
                )
                .values(revoked=True, data=b"{}")
                .returning(BrowserSessionRow.token_hash)
            ).scalar_one_or_none()
        return accepted is not None
