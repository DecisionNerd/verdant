"""Idempotent test-only fixture scope repair; never target an organizational realm."""

import json
from pathlib import Path

import httpx

base = "http://127.0.0.1:18081"
realm = json.loads(Path(__file__).with_name("realm.json").read_text())
with httpx.Client(base_url=base, timeout=10) as client:
    response = client.post(
        "/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": "verdant-test-admin",
            "password": "verdant-test-only-admin",
        },
    )
    response.raise_for_status()
    client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
    root = "/admin/realms/verdant-qualification"
    scopes = {s["name"]: s for s in client.get(root + "/client-scopes").json()}
    for scope in realm["clientScopes"]:
        if scope["name"] not in scopes:
            result = client.post(root + "/client-scopes", json=scope)
            result.raise_for_status()
    scopes = {s["name"]: s for s in client.get(root + "/client-scopes").json()}
    for fixture in realm["clients"]:
        configured = client.get(root + "/clients", params={"clientId": fixture["clientId"]}).json()[
            0
        ]
        for name in fixture.get("defaultClientScopes", []):
            result = client.put(
                root + f"/clients/{configured['id']}/default-client-scopes/{scopes[name]['id']}"
            )
            result.raise_for_status()
print("Test realm scopes synchronized; subjects preserved")
