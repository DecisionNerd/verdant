"""Exercise the isolated Keycloak fixture. No production credentials or realm support."""

import os
from uuid import UUID, uuid4

import httpx

from verdant.access_tokens import AccessTokenSettings, AccessTokenVerifier
from verdant.identity import IdentityService
from verdant.persistence import NamespaceRow, make_sessions

ISSUER = "http://127.0.0.1:18081/realms/verdant-qualification"
API = "http://127.0.0.1:8788"
NAMESPACE = UUID("d6251e45-50b7-4618-b35c-ae245c14a395")

url = os.environ["VERDANT_DATABASE_URL"]
if not url.endswith("/verdant_issue11_workspace"):
    raise RuntimeError("Qualification requires the isolated verdant_issue11_workspace database")
sessions = make_sessions(url)
with httpx.Client(timeout=10) as client:
    result = client.post(
        ISSUER + "/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "verdant-worker",
            "client_secret": "verdant-test-only-worker-secret",
        },
    )
    result.raise_for_status()
    token = result.json()["access_token"]
    verifier = AccessTokenVerifier(
        AccessTokenSettings(
            issuer=ISSUER,
            audience="verdant-api",
            clients=frozenset({"verdant-worker"}),
            allow_insecure_http=True,
        )
    )
    claims = verifier.verify(token)
    identity = IdentityService(sessions)
    principal = identity.provision(ISSUER, claims["sub"], kind="service")
    with sessions.begin() as session:
        if session.get(NamespaceRow, NAMESPACE) is None:
            session.add(NamespaceRow(id=NAMESPACE, name="Identity qualification"))
    identity.grant(principal, NAMESPACE, "write")
    headers = {"authorization": "Bearer " + token, "accept": "application/json, text/event-stream"}
    result = client.post(
        API + f"/api/v1/namespaces/{NAMESPACE}/datasets",
        headers=headers,
        json={
            "slug": "live-" + str(uuid4()),
            "name": "Live identity proof",
            "modality": "document",
        },
    )
    assert result.status_code == 201, result.status_code
    dataset = result.json()
    assert dataset["owner_id"] == str(principal)
    result = client.post(
        API + "/mcp/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "qualification", "version": "1"},
            },
        },
    )
    assert result.status_code == 200, result.status_code
    result = client.post(
        API + "/mcp/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "dataset_get",
                "arguments": {"namespace_id": str(NAMESPACE), "dataset_id": dataset["id"]},
            },
        },
    )
    assert result.status_code == 200, result.status_code
    assert result.json()["result"]["structuredContent"]["owner_id"] == str(principal)
    denied = uuid4()
    assert (
        client.get(
            API + f"/api/v1/namespaces/{denied}/datasets/{dataset['id']}", headers=headers
        ).status_code
        == 403
    )
    assert (
        client.get(API + f"/api/v1/namespaces/{NAMESPACE}/datasets/{dataset['id']}").status_code
        == 401
    )
    # A current local disable must defeat an already-issued, correctly signed token.
    identity.set_enabled(principal, False)
    try:
        assert (
            client.get(
                API + f"/api/v1/namespaces/{NAMESPACE}/datasets/{dataset['id']}", headers=headers
            ).status_code
            == 403
        )
        assert (
            client.post(
                API + "/mcp/",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            ).status_code
            == 401
        )
    finally:
        identity.set_enabled(principal, True)
print(
    "Live Keycloak service token: REST create, MCP read, owner attribution, "
    "namespace denial, unauthenticated denial and local disable passed"
)
