import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from verdant.application import RegistryService
from verdant.identity import IdentityService
from verdant.persistence import NamespaceRow, make_sessions


@pytest.fixture(scope="session")
def sessions():
    url = os.environ.get("VERDANT_TEST_DATABASE_URL")
    if not url or not make_url(url).database.endswith("_test"):
        pytest.fail("Set VERDANT_TEST_DATABASE_URL to an isolated PostgreSQL database ending _test")
    os.environ["VERDANT_DATABASE_URL"] = url
    command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")
    factory = make_sessions(url)
    yield factory
    factory.kw["bind"].dispose()


@pytest.fixture
def principal_factory(sessions):
    identities = IdentityService(sessions)

    def create(namespace=None, access="write"):
        subject = str(uuid4())
        identity = identities.provision("https://test-issuer", subject, kind="human")
        if namespace is not None:
            identities.grant(identity, namespace, access)
        return identities.resolve("https://test-issuer", subject)

    return create


@pytest.fixture
def registry(sessions, principal_factory):
    namespace = uuid4()
    with sessions.begin() as session:
        session.add(NamespaceRow(id=namespace, name="test"))
    actor = principal_factory(namespace)
    return RegistryService(sessions), actor, namespace


@pytest.fixture
def storage():
    from verdant.storage import S3Settings, S3Storage

    endpoint = os.environ.get("VERDANT_STORAGE_TEST_ENDPOINT")
    if not endpoint:
        pytest.skip("Explicit disposable S3 contract target is not configured")
    adapter = S3Storage(
        S3Settings(
            bucket=os.environ["VERDANT_STORAGE_TEST_BUCKET"],
            endpoint=endpoint,
            browser_endpoint=os.environ.get("VERDANT_STORAGE_TEST_BROWSER_ENDPOINT", endpoint),
            region=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
            allow_insecure_http=os.environ.get("VERDANT_STORAGE_TEST_LOCAL_HTTP") == "1",
        )
    )
    prefix = "qualification/issue14/" + uuid4().hex + "/"
    yield adapter, prefix
    continuation = None
    keys = []
    while True:
        page = adapter.list_page(prefix, continuation=continuation)
        keys.extend(item["Key"] for item in page.get("Contents", []))
        if not page.get("IsTruncated"):
            break
        continuation = page["NextContinuationToken"]
    for offset in range(0, len(keys), 100):
        adapter.delete(keys[offset : offset + 100])
