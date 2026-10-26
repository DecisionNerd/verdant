import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from verdant.application import RegistryService
from verdant.domain import Principal
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
def registry(sessions):
    namespace = uuid4()
    with sessions.begin() as session:
        session.add(NamespaceRow(id=namespace, name="test"))
    actor = Principal(uuid4(), writable_namespaces=frozenset({namespace}))
    return RegistryService(sessions), actor, namespace
