"""Actual PostgreSQL/S3 transport composition; signed service JWT/JWKS are fixtures."""

import hashlib
from time import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select

from verdant.api import create_app
from verdant.auth import IdentitySettings
from verdant.identity import IdentityService
from verdant.ingestion_runner import IngestionRunner
from verdant.persistence import AuditRow, IngestionItemRow


@pytest.fixture
def upload_client(sessions, registry, storage, monkeypatch):
    _, _, namespace = registry
    adapter, _ = storage
    settings = IdentitySettings(
        issuer="https://issuer.test/realms/verdant",
        client_id="browser",
        client_secret="test-only-browser",
        app_url="https://workspace.test",
        admission_group="/workspace",
        audience="verdant-api",
        service_clients=frozenset({"worker"}),
    )
    app = create_app(sessions, settings, storage=adapter)
    runtime = app.state.identity
    subject = str(uuid4())
    principal_id = runtime.identities.provision(settings.issuer, subject, kind="service")
    runtime.identities.grant(principal_id, namespace, "write")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        runtime.tokens.keys,
        "get_signing_key_from_jwt",
        lambda token: SimpleNamespace(key=key.public_key()),
    )
    token = jwt.encode(
        {
            "iss": settings.issuer,
            "sub": subject,
            "aud": settings.audience,
            "exp": int(time()) + 300,
            "iat": int(time()),
            "azp": "worker",
            "typ": "Bearer",
            "scope": "verdant",
        },
        key,
        algorithm="RS256",
        headers={"kid": "fixture"},
    )
    headers = {"authorization": "Bearer " + token, "accept": "application/json, text/event-stream"}
    with TestClient(app, base_url="https://workspace.test") as client:
        response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "ingestion-proof", "version": "1"},
                },
            },
            headers=headers,
        )
        assert response.status_code == 200
        yield client, headers, namespace, principal_id, adapter, app
    with sessions() as session:
        items = session.scalars(
            select(IngestionItemRow).where(IngestionItemRow.namespace_id == namespace)
        ).all()
    for item in items:
        if item.upload_id:
            adapter.abort(item.staging_key, item.upload_id)
        for upload_id in adapter.find_uploads(item.staging_key):
            adapter.abort(item.staging_key, upload_id)
        adapter.delete_one(item.staging_key)


def call_tool(client, headers, name, **arguments):
    response = client.post(
        "/mcp/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert not result.get("isError"), result
    return result["structuredContent"]


def command(body=b"transport verified bytes", **changes):
    return {
        "request_key": str(uuid4()),
        "items": [
            {
                "source_key": str(uuid4()),
                "modality": "text",
                "sha256": hashlib.sha256(body).hexdigest(),
                "size_bytes": len(body),
            }
        ],
    } | changes


@pytest.mark.parametrize("body", [b"transport verified bytes", b""])
def test_rest_mcp_share_real_upload_progress_and_verified_result(upload_client, sessions, body):
    client, headers, ns, principal_id, adapter, app = upload_client
    prefix = f"/api/v1/namespaces/{ns}/ingestions"
    limits = client.get(prefix + "/limits", headers=headers)
    assert limits.status_code == 200 and limits.json()["configured"]
    assert limits.json() == call_tool(client, headers, "ingestion_limits", namespace_id=str(ns))
    request = command(body)
    response = client.post(prefix + "/uploads", json=request, headers=headers)
    assert response.status_code == 202
    batch = response.json()
    operation, item = batch["operation"]["id"], batch["items"][0]["id"]
    initiated = call_tool(
        client,
        headers,
        "ingestion_upload_initiate",
        namespace_id=str(ns),
        operation_id=operation,
        item_id=item,
        epoch=1,
    )
    assert initiated["items"][0]["state"] == "uploading"
    assert (
        initiated
        == client.post(
            prefix + f"/{operation}/items/{item}/initiate", json={"epoch": 1}, headers=headers
        ).json()
    )
    signed = client.post(
        prefix + f"/{operation}/items/{item}/sign-part",
        json={"epoch": 1, "part_number": 1},
        headers=headers,
    )
    assert signed.status_code == 200 and signed.headers["cache-control"] == "no-store"
    mcp_signed = call_tool(
        client,
        headers,
        "ingestion_upload_sign_part",
        namespace_id=str(ns),
        operation_id=operation,
        item_id=item,
        command={"epoch": 1, "part_number": 1},
    )
    assert mcp_signed["size_bytes"] == signed.json()["size_bytes"]
    with httpx.Client(timeout=10, trust_env=False) as http:
        http.put(mcp_signed["url"], content=body).raise_for_status()
    parts = call_tool(
        client,
        headers,
        "ingestion_upload_parts",
        namespace_id=str(ns),
        operation_id=operation,
        item_id=item,
        epoch=1,
    )
    assert parts["parts"] == [{"part_number": 1, "size_bytes": len(body)}]
    assert (
        parts
        == client.get(prefix + f"/{operation}/items/{item}/parts?epoch=1", headers=headers).json()
    )
    completed = client.post(
        prefix + f"/{operation}/items/{item}/complete", json={"epoch": 1}, headers=headers
    )
    assert completed.status_code == 202 and completed.json()["operation"]["status"] == "queued"
    assert completed.json() == call_tool(
        client,
        headers,
        "ingestion_upload_complete",
        namespace_id=str(ns),
        operation_id=operation,
        item_id=item,
        epoch=1,
    )
    actor = app.state.identity.identities.resolve(
        app.state.identity.settings.issuer,
        next_identity_subject(sessions, principal_id),
        kind="service",
    )
    done = IngestionRunner(sessions, adapter).run(actor, ns, UUID(operation), 1)
    rest = client.get(prefix + f"/{operation}", headers=headers).json()
    mcp = call_tool(client, headers, "ingestion_get", namespace_id=str(ns), operation_id=operation)
    assert rest == mcp == done.model_dump(mode="json")
    assert rest["items"][0]["state"] == "accepted"
    listing = client.get(prefix, headers=headers).json()
    assert [row["id"] for row in listing["items"]] == [operation]
    assert listing == call_tool(client, headers, "ingestions_list", namespace_id=str(ns))
    with sessions() as session:
        audit = session.scalars(
            select(AuditRow).where(
                AuditRow.namespace_id == ns,
                AuditRow.action.in_(["operation.admitted", "operation.effect_prepared"]),
            )
        ).all()
        assert {row.payload["transport"] for row in audit} >= {"http-bearer", "mcp"}


def next_identity_subject(sessions, principal_id):
    from verdant.persistence import ExternalIdentityRow

    with sessions() as session:
        return session.scalar(
            select(ExternalIdentityRow.subject).where(
                ExternalIdentityRow.principal_id == principal_id
            )
        )


def test_ingestion_denial_bounds_and_cancel_match_across_transports(upload_client, sessions):
    client, headers, ns, principal_id, adapter, app = upload_client
    prefix = f"/api/v1/namespaces/{ns}/ingestions"
    assert client.post(prefix + "/uploads", json=command()).status_code == 401
    batch = call_tool(
        client, headers, "ingestion_upload_admit", namespace_id=str(ns), command=command()
    )
    operation, item = batch["operation"]["id"], batch["items"][0]["id"]
    path = prefix + f"/{operation}/items/{item}"
    assert (
        client.post(
            path + "/sign-part", json={"epoch": True, "part_number": 1}, headers=headers
        ).status_code
        == 422
    )
    assert (
        client.post(
            path + "/sign-part", json={"epoch": 1, "part_number": 33}, headers=headers
        ).status_code
        == 422
    )
    assert client.get(prefix + "?limit=101", headers=headers).status_code == 422
    denied = f"/api/v1/namespaces/{uuid4()}/ingestions/{operation}"
    assert client.get(denied, headers=headers).status_code == 403
    cancelled = call_tool(
        client, headers, "ingestion_cancel", namespace_id=str(ns), operation_id=operation, epoch=1
    )
    assert cancelled["operation"]["status"] == "cancellation_requested"
    assert (
        cancelled
        == client.post(prefix + f"/{operation}/cancel", json={"epoch": 1}, headers=headers).json()
    )
    IdentityService(sessions).revoke_grant(principal_id, ns)
    assert client.get(prefix + f"/{operation}", headers=headers).status_code == 403
    denied_mcp = client.post(
        "/mcp/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "ingestion_get",
                "arguments": {"namespace_id": str(ns), "operation_id": operation},
            },
        },
    )
    assert denied_mcp.json()["result"]["isError"]


def test_unconfigured_storage_denies_new_upload_but_allows_authorized_history_and_cancel(
    upload_client, sessions, monkeypatch
):
    client, headers, ns, principal_id, adapter, existing_app = upload_client
    prefix = f"/api/v1/namespaces/{ns}/ingestions"
    batch = client.post(prefix + "/uploads", json=command(), headers=headers).json()
    monkeypatch.delenv("VERDANT_S3_ENDPOINT", raising=False)
    app = create_app(sessions, existing_app.state.identity.settings)
    monkeypatch.setattr(
        app.state.identity.tokens.keys,
        "get_signing_key_from_jwt",
        existing_app.state.identity.tokens.keys.get_signing_key_from_jwt,
    )
    with TestClient(app, base_url="https://workspace.test") as offline:
        assert offline.get(prefix + "/limits", headers=headers).json()["configured"] is False
        denied = offline.post(prefix + "/uploads", json=command(), headers=headers)
        assert (
            denied.status_code == 503
            and denied.json()["detail"] == "Upload storage is not configured"
        )
        wrong_namespace = f"/api/v1/namespaces/{uuid4()}/ingestions/uploads"
        assert offline.post(wrong_namespace, json=command(), headers=headers).status_code == 403
        assert offline.get(prefix + f"/{batch['operation']['id']}", headers=headers).json() == batch
        response = offline.post(
            prefix + f"/{batch['operation']['id']}/cancel", json={"epoch": 1}, headers=headers
        )
        assert response.status_code == 202
        assert response.json()["operation"]["status"] == "cancellation_requested"
        assert len(offline.get(prefix, headers=headers).json()["items"]) == 1


def test_reupload_after_reload_derives_pins_and_exposes_recovery_links(upload_client, sessions):
    from verdant.application import RegistryService
    from verdant.domain import Principal
    from verdant.ingestion_runner import IngestionFailed

    client, headers, ns, principal_id, adapter, app = upload_client
    prefix = f"/api/v1/namespaces/{ns}/ingestions"
    body = b"correct"
    request = command(body)
    request["items"][0]["metadata"] = {"title": "retained only on server"}
    initial = client.post(prefix + "/uploads", json=request, headers=headers).json()

    def send(batch, payload):
        operation, item = batch["operation"]["id"], batch["items"][0]["id"]
        path = prefix + f"/{operation}/items/{item}"
        assert (
            client.post(path + "/initiate", json={"epoch": 1}, headers=headers).status_code == 200
        )
        signed = client.post(
            path + "/sign-part", json={"epoch": 1, "part_number": 1}, headers=headers
        ).json()
        with httpx.Client(timeout=10, trust_env=False) as http:
            http.put(signed["url"], content=payload).raise_for_status()
        assert (
            client.post(path + "/complete", json={"epoch": 1}, headers=headers).status_code == 202
        )

    send(initial, b"BROKEN!")
    actor = Principal(principal_id)
    runner = IngestionRunner(sessions, adapter)
    with pytest.raises(IngestionFailed, match="artifact_corrupt"):
        runner.run(actor, ns, UUID(initial["operation"]["id"]), 1)
    failed = client.get(prefix + f"/{initial['operation']['id']}", headers=headers).json()
    assert "metadata" not in failed["items"][0]
    # Reload recovery sends only origin identities; browser need not retain declaration JSON.
    recovery_request = {"request_key": str(uuid4()), "item_ids": [failed["items"][0]["id"]]}
    response = client.post(prefix + "/reuploads", json=recovery_request, headers=headers)
    assert response.status_code == 202
    recovery = response.json()
    assert recovery == call_tool(
        client, headers, "ingestion_reupload", namespace_id=str(ns), command=recovery_request
    )
    history = client.get(prefix + f"/{initial['operation']['id']}", headers=headers).json()
    assert history["items"][0]["replacement_operation_id"] == recovery["operation"]["id"]
    assert history["items"][0]["replacement_item_id"] == recovery["items"][0]["id"]
    send(recovery, body)
    accepted = runner.run(actor, ns, UUID(recovery["operation"]["id"]), 1).items[0]
    revision = RegistryService(sessions).get_sample_revision(
        actor, ns, accepted.sample_id, accepted.accepted_revision_id
    )
    assert revision.metadata == {"title": "retained only on server"}
