"""Actual PostgreSQL/S3 preview boundaries; no browser rendering proof implied."""

import base64
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from verdant.api import create_app, verified_principal
from verdant.contracts import SampleCreate
from verdant.domain import Forbidden, NotFound
from verdant.identity import IdentityService
from verdant.ingestion_runner import IngestionRunner
from verdant.persistence import ArtifactLocationRow, NamespaceGrantRow
from verdant.preview import TEXT_BYTES, PreviewService
from verdant.storage import ObjectCorrupt, ObjectMissing


def accept(ready_ingestion, sessions, body):
    create, actor, ns, adapter = ready_ingestion
    _, batch = create([("source", body)])
    item = IngestionRunner(sessions, adapter).run(actor, ns, batch.operation.id, 1).items[0]
    return PreviewService(sessions, adapter), actor, ns, item, adapter


def test_exact_text_preview_and_attachment_survive_new_revision(ready_ingestion, sessions):
    preview, actor, ns, item, _ = accept(ready_ingestion, sessions, b"<script>alert(1)</script>")
    original = preview.get(actor, ns, item.sample_id, item.accepted_revision_id)
    assert original.kind == "text" and original.text == "<script>alert(1)</script>"
    assert not original.truncated
    with httpx.Client(trust_env=False) as http:
        response = http.get(original.download_url)
    assert response.content == b"<script>alert(1)</script>"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == "attachment"
    create, _, _, adapter = ready_ingestion
    _, batch = create([("source", b"new bytes")], target=(item.sample_id, 1))
    new = IngestionRunner(sessions, adapter).run(actor, ns, batch.operation.id, 1).items[0]
    assert preview.get(actor, ns, new.sample_id, new.accepted_revision_id).text == "new bytes"
    assert preview.get(actor, ns, item.sample_id, item.accepted_revision_id).text == original.text


def test_bounded_utf8_preview_does_not_split_codepoint(ready_ingestion, sessions):
    body = ("€" * TEXT_BYTES).encode()
    preview, actor, ns, item, _ = accept(ready_ingestion, sessions, body)
    result = preview.get(actor, ns, item.sample_id, item.accepted_revision_id)
    assert result.kind == "text" and result.truncated
    assert result.text == "€" * (TEXT_BYTES // 3)
    assert len(result.text.encode()) <= TEXT_BYTES


def test_image_is_bounded_reencoded_png_without_source_metadata(ready_ingestion, sessions):
    image = Image.new("RGB", (1800, 1200), "red")
    source = BytesIO()
    exif = Image.Exif()
    exif[270] = "private source metadata"
    image.save(source, format="JPEG", exif=exif)
    preview, actor, ns, item, _ = accept(ready_ingestion, sessions, source.getvalue())
    result = preview.get(actor, ns, item.sample_id, item.accepted_revision_id)
    assert result.kind == "image" and result.text is None
    payload = base64.b64decode(result.image_png_base64)
    assert b"private source metadata" not in payload
    with Image.open(BytesIO(payload)) as output:
        assert output.format == "PNG" and max(output.size) == 1024
        assert not output.getexif()


@pytest.mark.parametrize("body", [b"%PDF-1.7\nfixture", b"\x00binary\xff", b"\xff\xfe"])
def test_unsupported_content_downloads_without_inline_execution(ready_ingestion, sessions, body):
    preview, actor, ns, item, _ = accept(ready_ingestion, sessions, body)
    result = preview.get(actor, ns, item.sample_id, item.accepted_revision_id)
    assert result.kind == "download" and result.text is None and result.image_png_base64 is None


def test_unverified_legacy_revision_never_reads_storage(registry, storage, monkeypatch):
    registry, actor, ns = registry
    adapter, _ = storage
    sample = registry.create_sample(
        actor,
        ns,
        SampleCreate(
            source_key="legacy",
            modality="text",
            object_key="private/unowned",
            sha256="a" * 64,
            size_bytes=1,
        ),
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Unverified declaration reached object storage")

    monkeypatch.setattr(adapter, "verify", forbidden)
    with pytest.raises(NotFound):
        PreviewService(registry.sessions, adapter).get(actor, ns, sample.sample_id, sample.id)


def test_denial_before_io_and_revocation_after_io(
    ready_ingestion, sessions, principal_factory, monkeypatch
):
    preview, actor, ns, item, adapter = accept(ready_ingestion, sessions, b"private")
    outsider = principal_factory()
    original = adapter.verify
    calls = []

    def revoke(*args, **kwargs):
        calls.append(True)
        result = original(*args, **kwargs)
        with sessions.begin() as session:
            session.delete(
                session.scalar(
                    select(NamespaceGrantRow).where(
                        NamespaceGrantRow.namespace_id == ns,
                        NamespaceGrantRow.principal_id == actor.id,
                    )
                )
            )
        return result

    monkeypatch.setattr(adapter, "verify", revoke)
    with pytest.raises(Forbidden):
        preview.get(outsider, ns, item.sample_id, item.accepted_revision_id)
    assert not calls
    with pytest.raises(Forbidden):
        preview.get(actor, ns, item.sample_id, item.accepted_revision_id)
    assert calls == [True]


def test_corrupt_missing_and_cross_sample_revision_denied(ready_ingestion, sessions):
    preview, actor, ns, item, adapter = accept(ready_ingestion, sessions, b"verified")
    with pytest.raises(NotFound):
        preview.get(actor, ns, uuid4(), item.accepted_revision_id)
    with sessions() as session:
        key = session.scalar(
            select(ArtifactLocationRow.object_key).where(
                ArtifactLocationRow.artifact_id == item.artifact_id
            )
        )
    # Privileged test-only mutations establish read-time integrity checks, not user repair.
    adapter.client.put_object(Bucket=adapter.settings.bucket, Key=key, Body=b"corrupt!")
    with pytest.raises(ObjectCorrupt):
        preview.get(actor, ns, item.sample_id, item.accepted_revision_id)
    adapter.delete_one(key)
    with pytest.raises(ObjectMissing):
        preview.get(actor, ns, item.sample_id, item.accepted_revision_id)
    adapter.client.put_object(Bucket=adapter.settings.bucket, Key=key, Body=b"verified")


def test_rest_preview_no_store_and_current_authorization(ready_ingestion, sessions, monkeypatch):
    preview, actor, ns, item, adapter = accept(ready_ingestion, sessions, b"REST text")
    monkeypatch.delenv("VERDANT_OIDC_ISSUER", raising=False)
    app = create_app(sessions, storage=adapter)
    app.dependency_overrides[verified_principal] = lambda: actor
    path = (
        f"/api/v1/namespaces/{ns}/samples/{item.sample_id}"
        f"/revisions/{item.accepted_revision_id}/preview"
    )
    with TestClient(app) as client:
        response = client.get(path)
        assert response.status_code == 200 and response.json()["text"] == "REST text"
        assert response.headers["cache-control"] == "no-store"
        IdentityService(sessions).revoke_grant(actor.id, ns)
        assert client.get(path).status_code == 403


def test_webp_download_only_and_oversized_image_denied_before_load(
    ready_ingestion, sessions, monkeypatch
):
    image = Image.new("RGB", (4, 4), "red")
    source = BytesIO()
    image.save(source, format="WEBP")
    preview, actor, ns, item, _ = accept(ready_ingestion, sessions, source.getvalue())
    result = preview.get(actor, ns, item.sample_id, item.accepted_revision_id)
    assert result.kind == "download"
    from PIL import PngImagePlugin

    source = BytesIO()
    image.save(source, format="PNG")
    original = PngImagePlugin.PngImageFile._open

    def oversized(self):
        original(self)
        self._size = (5000, 5000)

    def no_load(self, *args, **kwargs):
        pytest.fail("Oversized image reached pixel decoder")

    monkeypatch.setattr(PngImagePlugin.PngImageFile, "_open", oversized)
    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", no_load)
    result = PreviewService._image(BytesIO(source.getvalue()))
    assert result["kind"] == "download" and "pixel" in result["reason"]
