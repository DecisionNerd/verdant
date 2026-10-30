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


@pytest.fixture
def ready_ingestion(registry, sessions, storage):
    import hashlib

    import httpx

    from verdant.ingestion import IngestionService, UploadDeclaration, UploadRequest
    from verdant.persistence import IngestionItemRow

    _, actor, ns = registry
    adapter, _ = storage
    owned = []

    def create(entries=None, *, target=None, metadata=None, retry_of=None, uploaded_bodies=None):
        entries = entries or [("source", b"verified content")]
        service = IngestionService(sessions, bucket=adapter.settings.bucket, storage=adapter)
        batch = service.admit(
            actor,
            ns,
            UploadRequest(
                request_key=str(uuid4()),
                items=[
                    UploadDeclaration(
                        source_key=name,
                        modality="text",
                        sha256=hashlib.sha256(body).hexdigest(),
                        size_bytes=len(body),
                        metadata=metadata or {},
                        target_sample_id=target[0] if target else None,
                        expected_revision=target[1] if target else None,
                        retry_of_item_id=retry_of,
                    )
                    for name, body in entries
                ],
            ),
        )
        for item, (name, body) in zip(batch.items, entries, strict=True):
            service.initiate_upload(actor, ns, batch.operation.id, item.id, 1)
            with sessions() as session:
                stored = session.get(IngestionItemRow, item.id)
                owned.append((stored.staging_key, stored.upload_id))
            signed = service.sign_upload_part(actor, ns, batch.operation.id, item.id, 1, 1)
            with httpx.Client(timeout=10, trust_env=False) as http:
                uploaded = (uploaded_bodies or {}).get(name, body)
                http.put(signed.url, content=uploaded).raise_for_status()
            batch = service.complete_upload(actor, ns, batch.operation.id, item.id, 1)
        return service, batch

    yield create, actor, ns, adapter
    # Retain accepted final bytes with their persistent test-database references.
    for key, upload_id in owned:
        adapter.abort(key, upload_id)
        adapter.delete_one(key)
