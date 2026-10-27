import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from secrets import token_hex
from threading import Barrier

from verdant.browser_sessions import DatabaseSessionStore, digest
from verdant.persistence import BrowserSessionRow, LoginAttemptRow


def test_database_sessions_keep_tokens_opaque_and_cannot_revive_logout(sessions):
    store = DatabaseSessionStore(sessions)
    token = token_hex(16)
    asyncio.run(store.write(token, b'{"principal":"test"}', lifetime=1800, ttl=1800))
    assert asyncio.run(store.read(token, lifetime=1800)) == b'{"principal":"test"}'
    with sessions() as session:
        row = session.get(BrowserSessionRow, digest(token))
        assert row.token_hash != token
        deadline = row.expires_at
    asyncio.run(store.write(token, b'{"principal":"updated"}', lifetime=1800, ttl=3600))
    with sessions() as session:
        assert session.get(BrowserSessionRow, digest(token)).expires_at == deadline
    asyncio.run(store.remove(token))
    # Simulate a request which loaded the session before another request logged out.
    asyncio.run(store.write(token, b'{"principal":"test"}', lifetime=1800, ttl=1800))
    assert asyncio.run(store.read(token, lifetime=1800)) == b"{}"
    assert asyncio.run(store.read("untrusted-cookie", lifetime=1800)) == b"{}"


def test_login_attempt_is_browser_bound_single_use_and_expiring(sessions):
    store = DatabaseSessionStore(sessions)
    state, browser = token_hex(16), token_hex(16)
    store.start_attempt(state, browser)
    assert not store.consume_attempt(state, token_hex(16))
    barrier = Barrier(2)

    def consume(_):
        barrier.wait(timeout=10)
        return store.consume_attempt(state, browser)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(consume, range(2))) == [False, True]
    assert not store.consume_attempt(state, browser)
    expired = token_hex(16)
    store.start_attempt(expired, browser)
    with sessions.begin() as session:
        session.get(LoginAttemptRow, digest(expired)).expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
    assert not store.consume_attempt(expired, browser)
    store.cleanup_expired()
    with sessions() as session:
        assert session.get(LoginAttemptRow, digest(expired)) is None
