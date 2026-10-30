from time import time
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from verdant.api import create_app
from verdant.auth import IdentitySettings


@pytest.fixture
def login_app(sessions, monkeypatch):
    settings = IdentitySettings(
        issuer="https://issuer.test/realms/verdant",
        client_id="browser",
        client_secret="test-only-client-secret",
        app_url="https://workspace.test",
        admission_group="/workspace",
        audience="verdant-api",
        service_clients=frozenset({"worker"}),
    )
    app = create_app(sessions, settings)
    runtime = app.state.identity
    runtime.oidc.server_metadata.update(
        {
            "issuer": settings.issuer,
            "authorization_endpoint": settings.issuer + "/authorize",
            "token_endpoint": settings.issuer + "/token",
            "_loaded_at": time(),
            "end_session_endpoint": settings.issuer + "/logout",
        }
    )
    claims = {"sub": str(uuid4()), "groups": ["/workspace"], "exp": int(time()) + 300}

    async def callback(request, **kwargs):
        # This fixture tests our session/route lifecycle, not provider protocol validation.
        return {"id_token": "test-only-validated-token", "userinfo": claims}

    monkeypatch.setattr(runtime.oidc, "authorize_access_token", callback)
    return app, claims


def begin_login(client):
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] and query["nonce"] and query["state"]
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "secure" in response.headers["set-cookie"].lower()
    assert "domain=" not in response.headers["set-cookie"].lower()
    return query["state"][0]


def test_browser_session_rotation_csrf_logout_and_replay(login_app, registry):
    app, _ = login_app
    with TestClient(app, base_url="https://workspace.test") as client:
        assert client.get("/auth/session").json() == {"authenticated": False}
        assert (
            client.get("/auth/login", params={"next": "https://attacker.test"}).status_code == 400
        )
        state = begin_login(client)
        before = client.cookies.get("verdant_session")
        response = client.get(
            "/auth/callback", params={"state": state, "code": "fixture"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert client.cookies.get("verdant_session") != before
        authenticated_cookie = client.cookies.get("verdant_session")
        session_response = client.get("/auth/session")
        assert session_response.headers["cache-control"] == "no-store"
        session = session_response.json()
        assert session["authenticated"]
        assert (
            client.get("/auth/callback", params={"state": state, "code": "fixture"}).status_code
            == 400
        )
        path = f"/api/v1/namespaces/{uuid4()}/datasets"
        body = {"slug": "denied", "name": "Denied", "modality": "image"}
        assert client.post(path, json=body).status_code == 403
        _, _, namespace = registry
        actor_id = UUID(session["principal_id"])
        before_grant = client.post(
            f"/api/v1/namespaces/{namespace}/datasets",
            json={"slug": "browser-created", "name": "Browser", "modality": "image"},
            headers={"origin": "https://workspace.test", "x-csrf-token": session["csrf_token"]},
        )
        assert before_grant.status_code == 403
        assert before_grant.json()["detail"] == "Namespace access denied"
        app.state.identity.identities.grant(actor_id, namespace, "write")
        created = client.post(
            f"/api/v1/namespaces/{namespace}/datasets",
            json={
                "slug": "browser-created",
                "name": "Browser",
                "modality": "image",
            },
            headers={
                "origin": "https://workspace.test",
                "x-csrf-token": session["csrf_token"],
                "X-Transport": "mcp",
            },
        )
        assert created.status_code == 201
        assert created.json()["owner_id"] == str(actor_id)
        from sqlalchemy import select

        from verdant.persistence import AuditRow

        with app.state.identity.sessions() as database:
            row = database.scalar(
                select(AuditRow).where(AuditRow.aggregate_id == UUID(created.json()["id"]))
            )
            assert row.actor_id == actor_id and row.payload["transport"] == "browser"
            login_event = database.scalar(
                select(AuditRow).where(
                    AuditRow.aggregate_id == actor_id,
                    AuditRow.action == "identity.provisioned",
                )
            )
            assert login_event.payload["transport"] == "oidc"
        assert client.post("/auth/logout").status_code == 403
        response = client.post(
            "/auth/logout",
            headers={
                "origin": "https://workspace.test",
                "x-csrf-token": session["csrf_token"],
            },
        )
        assert response.status_code == 200
        assert response.json()["logout_url"].startswith(
            "https://issuer.test/realms/verdant/logout?"
        )
        assert client.get("/auth/session").json() == {"authenticated": False}
        # A copied old cookie cannot recover a logged-out session.
        client.cookies.set("verdant_session", authenticated_cookie)
        assert client.get("/auth/session").json() == {"authenticated": False}


def test_unadmitted_login_does_not_create_principal(login_app, sessions):
    from sqlalchemy import func, select

    from verdant.persistence import ExternalIdentityRow

    app, claims = login_app
    claims["groups"] = []
    with TestClient(app, base_url="https://workspace.test") as client:
        state = begin_login(client)
        assert (
            client.get("/auth/callback", params={"state": state, "code": "fixture"}).status_code
            == 403
        )
        assert client.get("/auth/session").json() == {"authenticated": False}
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ExternalIdentityRow)
                .where(
                    ExternalIdentityRow.subject == claims["sub"],
                )
            )
            == 0
        )


def test_api_mcp_share_service_identity_and_namespace_denial(login_app, registry, monkeypatch):
    from types import SimpleNamespace

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    app, _ = login_app
    _, _, ns = registry
    runtime = app.state.identity
    subject = str(uuid4())
    service_id = runtime.identities.provision(runtime.settings.issuer, subject, kind="service")
    runtime.identities.grant(service_id, ns, "write")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        runtime.tokens.keys,
        "get_signing_key_from_jwt",
        lambda token: SimpleNamespace(key=key.public_key()),
    )
    claims = {
        "iss": runtime.settings.issuer,
        "sub": subject,
        "aud": runtime.settings.audience,
        "exp": int(time()) + 300,
        "iat": int(time()),
        "azp": "worker",
        "typ": "Bearer",
        "scope": "verdant",
    }
    bearer = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "fixture-key"})
    headers = {
        "authorization": "Bearer " + bearer,
        "accept": "application/json, text/event-stream",
        "X-Transport": "browser",
    }
    with TestClient(app, base_url="https://workspace.test") as client:
        response = client.post(
            f"/api/v1/namespaces/{ns}/datasets",
            json={
                "slug": "service",
                "name": "Service",
                "modality": "image",
            },
            headers=headers,
        )
        assert response.status_code == 201
        d = response.json()
        assert d["owner_id"] == str(service_id)
        assert (
            client.post(
                "/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "proof", "version": "1"},
                    },
                },
                headers=headers,
            ).status_code
            == 200
        )
        response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "dataset_get",
                    "arguments": {"namespace_id": str(ns), "dataset_id": d["id"]},
                },
            },
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["result"]["structuredContent"]["id"] == d["id"]
        response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "dataset_create",
                    "arguments": {
                        "namespace_id": str(ns),
                        "command": {
                            "slug": "mcp-service",
                            "name": "MCP",
                            "modality": "image",
                        },
                    },
                },
            },
            headers=headers,
        )
        assert not response.json()["result"].get("isError")
        from sqlalchemy import select

        from verdant.persistence import AuditRow

        with runtime.sessions() as session:
            rows = session.scalars(
                select(AuditRow).where(
                    AuditRow.namespace_id == ns,
                    AuditRow.actor_id == service_id,
                    AuditRow.action == "dataset.created",
                )
            ).all()
            assert {row.payload["transport"] for row in rows} == {"http-bearer", "mcp"}
        # Candidate transport parity includes actual non-null interpretation, not just IDs.
        from verdant.application import RegistryService
        from verdant.contracts import InlineEvidence, SampleCreate

        actor = runtime.identities.resolve(runtime.settings.issuer, subject, kind="service")
        sample = RegistryService(runtime.sessions).create_sample(
            actor,
            ns,
            SampleCreate(
                source_key="transport-pins",
                modality="text",
                sha256="a" * 64,
                object_key="private/transport",
                size_bytes=1,
            ),
        )
        annotation = InlineEvidence(format="test.annotation.v1", payload={"text": "Pinned"})
        pins = {
            "annotation_revision": annotation.model_dump(),
            "task_schema_revision": {"format": "test.schema.v1", "payload": {"label": "text"}},
            "review_evidence": {
                "annotation_digest": annotation.sha256,
                "decision": "accepted",
                "rationale": "Input review, not a policy gate",
            },
            "transformation": {
                "payload": {
                    "code": "text.strip()",
                    "configuration": {},
                    "environment": {"python": "3.12"},
                }
            },
        }
        base = f"/api/v1/namespaces/{ns}/datasets/{d['id']}"
        assert (
            client.put(
                base + f"/draft/members/{sample.sample_id}",
                headers=headers,
                json={
                    "expected_revision": 0,
                    "sample_revision_id": str(sample.id),
                    "split": "train",
                    "interpretation": pins,
                },
            ).status_code
            == 200
        )
        captured = client.post(
            base + "/candidates",
            headers=headers,
            json={"request_key": "transport-capture", "expected_revision": 1},
        )
        assert captured.status_code == 201
        candidate_id = captured.json()["id"]
        arguments = {"namespace_id": str(ns), "dataset_id": d["id"], "candidate_id": candidate_id}
        prepared = client.post(
            "/mcp/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 20,
                "method": "tools/call",
                "params": {
                    "name": "candidate_prepare",
                    "arguments": arguments | {"command": {"expected_count": 0, "limit": 1}},
                },
            },
        )
        result = prepared.json()["result"]
        assert not result.get("isError"), result
        assert result["structuredContent"]["state"] == "sealed"
        mcp_members = client.post(
            "/mcp/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 21,
                "method": "tools/call",
                "params": {"name": "candidate_members", "arguments": arguments},
            },
        )
        members = mcp_members.json()["result"]["structuredContent"]
        http_members = client.get(
            base + f"/candidates/{candidate_id}/members", headers=headers
        ).json()
        assert members == http_members
        assert (
            members["items"][0]["interpretation"]["annotation_revision"]["sha256"]
            == annotation.sha256
        )
        assert members["items"][0]["interpretation"]["schema"] == "verdant.membership.v1"
        invalid_page = client.post(
            "/mcp/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 22,
                "method": "tools/call",
                "params": {"name": "candidate_members", "arguments": arguments | {"limit": 0}},
            },
        )
        assert invalid_page.json()["result"]["isError"]
        # Quality REST/MCP parity pins an immutable policy/report to this exact generation.
        quality_args = {"namespace_id": str(ns), "dataset_id": d["id"]}

        def quality_tool(name, arguments):
            response = client.post(
                "/mcp/",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 23,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": quality_args | arguments},
                },
            )
            assert response.status_code == 200
            result = response.json()["result"]
            assert not result.get("isError"), result
            return result["structuredContent"]

        policy = quality_tool(
            "quality_policy_create",
            {
                "command": {
                    "request_key": "transport-policy",
                    "name": "transport",
                    "expected_revision": 0,
                    "definition": {
                        "rules": [{"id": "size", "kind": "minimum_samples", "minimum": 1}]
                    },
                }
            },
        )
        assert (
            policy == client.get(base + f"/quality/policies/{policy['id']}", headers=headers).json()
        )
        quality_report = quality_tool(
            "quality_validation_start",
            {
                "candidate_id": candidate_id,
                "command": {"request_key": "transport-report", "policy_id": policy["id"]},
            },
        )
        completed = quality_tool(
            "quality_validation_advance",
            {
                "report_id": quality_report["id"],
                "command": {"expected_count": 0, "limit": 1},
            },
        )
        assert completed["required_rules_pass"] is True
        assert completed["generation_id"] == members["candidate"]["generation_id"]
        assert (
            completed
            == client.get(base + f"/quality/reports/{completed['id']}", headers=headers).json()
        )
        assert quality_tool("quality_report_get", {"report_id": completed["id"]}) == completed
        denied = str(uuid4())
        assert (
            client.get(
                f"/api/v1/namespaces/{denied}/datasets/{d['id']}", headers=headers
            ).status_code
            == 403
        )
        response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "dataset_get",
                    "arguments": {"namespace_id": denied, "dataset_id": d["id"]},
                },
            },
            headers=headers,
        )
        assert response.json()["result"]["isError"]
        assert (
            client.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
                headers={
                    "accept": headers["accept"],
                },
            ).status_code
            == 401
        )
        wrong = jwt.encode(
            claims | {"aud": "wrong"}, key, algorithm="RS256", headers={"kid": "fixture-key"}
        )
        assert (
            client.get(
                f"/api/v1/namespaces/{ns}/datasets/{d['id']}",
                headers={"authorization": "Bearer " + wrong},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 5, "method": "tools/list"},
                headers={
                    "accept": headers["accept"],
                    "authorization": "Bearer " + wrong,
                },
            ).status_code
            == 401
        )


def test_account_switch_is_explicit_and_forces_provider_login(login_app):
    app, _ = login_app
    with TestClient(app, base_url="https://workspace.test") as client:
        response = client.get("/auth/login?switch_account=1", follow_redirects=False)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert query["prompt"] == ["login"]


def test_disable_then_reenable_does_not_revive_browser_session(login_app):
    from uuid import UUID

    app, _ = login_app
    with TestClient(app, base_url="https://workspace.test") as client:
        state = begin_login(client)
        assert (
            client.get(
                "/auth/callback", params={"state": state, "code": "fixture"}, follow_redirects=False
            ).status_code
            == 303
        )
        actor = UUID(client.get("/auth/session").json()["principal_id"])
        app.state.identity.identities.set_enabled(actor, False)
        app.state.identity.identities.set_enabled(actor, True)
        assert client.get("/auth/session").json() == {"authenticated": False}


def test_superseded_callback_cannot_mint_authenticated_session(login_app, monkeypatch):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    app, claims = login_app
    entered, release = Event(), Event()

    async def paused_exchange(request, **kwargs):
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        return {"id_token": "test-only-validated-token", "userinfo": claims}

    monkeypatch.setattr(app.state.identity.oidc, "authorize_access_token", paused_exchange)
    with TestClient(app, base_url="https://workspace.test") as client:
        state = begin_login(client)
        cookie = client.cookies.get("verdant_session")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                client.get,
                "/auth/callback",
                params={"state": state, "code": "fixture"},
                follow_redirects=False,
            )
            assert entered.wait(10)
            # A second browser request still carries the original pre-login cookie.
            with TestClient(app, base_url="https://workspace.test") as second:
                second.cookies.set("verdant_session", cookie)
                assert (
                    second.get("/auth/login?switch_account=1", follow_redirects=False).status_code
                    == 302
                )
            release.set()
            assert future.result(timeout=10).status_code == 400
        assert client.get("/auth/session").json() == {"authenticated": False}


@pytest.mark.parametrize(
    "change",
    [
        "valid",
        "issuer",
        "audience",
        "nonce",
        "expired",
        "azp",
        "signature",
        "missing_id_token",
    ],
)
def test_real_authlib_callback_validates_signed_id_token(login_app, monkeypatch, change):
    import json

    import jwt
    from authlib.integrations.starlette_client import StarletteOAuth2App
    from cryptography.hazmat.primitives.asymmetric import rsa

    app, admitted = login_app
    runtime = app.state.identity
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    public["kid"] = "test-key"
    nonce = ""

    async def jwks(force=False):
        return {"keys": [public]}

    async def exchange(**kwargs):
        payload = {
            "iss": runtime.settings.issuer,
            "sub": admitted["sub"],
            "aud": runtime.settings.client_id,
            "azp": runtime.settings.client_id,
            "iat": int(time()),
            "exp": int(time()) + 120,
            "nonce": nonce,
            "groups": ["/workspace"],
        }
        if change == "issuer":
            payload["iss"] = "https://other.test/realms/wrong"
        elif change == "audience":
            payload["aud"] = "another-client"
        elif change == "nonce":
            payload["nonce"] = "unrelated-nonce"
        elif change == "expired":
            payload["exp"] = int(time()) - 60
        elif change == "azp":
            payload["azp"] = "another-client"
        key = signing_key
        if change == "signature":
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = {"access_token": "fixture-access-token", "token_type": "Bearer"}
        if change != "missing_id_token":
            token["id_token"] = jwt.encode(
                payload, key, algorithm="RS256", headers={"kid": "test-key"}
            )
        return token

    # Stub only issuer HTTP responses; use the real Authlib callback and claim/signature checks.
    monkeypatch.setattr(
        runtime.oidc,
        "authorize_access_token",
        StarletteOAuth2App.authorize_access_token.__get__(runtime.oidc),
    )
    monkeypatch.setattr(runtime.oidc, "fetch_access_token", exchange)
    monkeypatch.setattr(runtime.oidc, "fetch_jwk_set", jwks)
    with TestClient(app, base_url="https://workspace.test") as client:
        response = client.get("/auth/login", follow_redirects=False)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        nonce = query["nonce"][0]
        response = client.get(
            "/auth/callback",
            params={"state": query["state"][0], "code": "fixture"},
            follow_redirects=False,
        )
        assert response.status_code == (303 if change == "valid" else 400)
        assert client.get("/auth/session").json()["authenticated"] == (change == "valid")


def test_optional_auth_telemetry_failure_does_not_bypass_admission(login_app, monkeypatch):
    from verdant import telemetry

    app, claims = login_app
    claims["groups"] = []

    class BrokenSignals:
        def start_span(self, *args, **kwargs):
            raise RuntimeError("collector unavailable")

        def add(self, *args, **kwargs):
            raise RuntimeError("collector unavailable")

    monkeypatch.setattr(telemetry, "tracer", BrokenSignals())
    monkeypatch.setattr(telemetry, "auth_failures", BrokenSignals())
    with TestClient(app, base_url="https://workspace.test") as client:
        state = begin_login(client)
        response = client.get("/auth/callback", params={"state": state, "code": "fixture"})
        assert response.status_code == 403
        assert client.get("/auth/session").json() == {"authenticated": False}


@pytest.mark.parametrize(
    "value,valid", [(" worker , ingest ", True), ("worker,,ingest", False), ("worker, ", False)]
)
def test_service_client_environment_normalization(monkeypatch, value, valid):
    values = {
        "VERDANT_OIDC_ISSUER": "https://issuer.test/realms/workspace",
        "VERDANT_OIDC_CLIENT_ID": "browser",
        "VERDANT_OIDC_CLIENT_SECRET": "fixture-only",
        "VERDANT_APP_URL": "https://workspace.test",
        "VERDANT_OIDC_GROUP": "/workspace",
        "VERDANT_API_AUDIENCE": "verdant-api",
        "VERDANT_SERVICE_CLIENTS": value,
    }
    for name, setting in values.items():
        monkeypatch.setenv(name, setting)
    if valid:
        assert IdentitySettings.from_env().service_clients == frozenset({"worker", "ingest"})
    else:
        with pytest.raises(ValueError):
            IdentitySettings.from_env()
