"""Rotate keys and stop ONLY the isolated issue-11 Keycloak fixture; always restore it."""

import base64
import json
import subprocess
import time
from uuid import uuid4

import httpx
import jwt

from verdant.access_tokens import AccessTokenSettings, AccessTokenVerifier, InvalidAccessToken

BASE = "http://127.0.0.1:18081"
REALM = "verdant-qualification"
ISSUER = BASE + "/realms/" + REALM
ROOT = "/admin/realms/" + REALM


def service_token(client):
    result = client.post(
        ISSUER + "/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "verdant-worker",
            "client_secret": "verdant-test-only-worker-secret",
        },
    )
    result.raise_for_status()
    return result.json()["access_token"]


def admin_headers(client):
    result = client.post(
        BASE + "/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": "verdant-test-admin",
            "password": "verdant-test-only-admin",
        },
    )
    result.raise_for_status()
    return {"authorization": "Bearer " + result.json()["access_token"]}


def reject(verifier, token):
    try:
        verifier.verify(token)
    except InvalidAccessToken:
        return
    raise AssertionError("Invalid or unknown key was accepted")


with httpx.Client(base_url=BASE, timeout=5) as client:
    headers = admin_headers(client)
    realm = client.get(ROOT, headers=headers)
    realm.raise_for_status()
    before = client.get(ROOT + "/users", headers=headers).json()
    subjects = {row["username"]: row["id"] for row in before}
    verifier = AccessTokenVerifier(
        AccessTokenSettings(
            issuer=ISSUER,
            audience="verdant-api",
            clients=frozenset({"verdant-worker"}),
            allow_insecure_http=True,
        )
    )
    fetch = verifier.keys.fetch_data
    calls = []

    def counted_fetch():
        calls.append(time.monotonic())
        return fetch()

    verifier.keys.fetch_data = counted_fetch
    old_token = service_token(client)
    verifier.verify(old_token)
    for _ in range(20):
        verifier.verify(old_token)
    assert len(calls) == 1
    name = "verdant-qualification-rotation-" + str(uuid4())
    result = client.post(
        ROOT + "/components",
        headers=headers,
        json={
            "name": name,
            "parentId": realm.json()["id"],
            "providerId": "rsa-generated",
            "providerType": "org.keycloak.keys.KeyProvider",
            "config": {
                "priority": ["1000000"],
                "enabled": ["true"],
                "active": ["true"],
                "keySize": ["2048"],
                "algorithm": ["RS256"],
            },
        },
    )
    result.raise_for_status()
    component = result.headers["location"].rsplit("/", 1)[-1]
    stopped = False
    try:
        new_token = service_token(client)
        assert (
            jwt.get_unverified_header(new_token)["kid"]
            != jwt.get_unverified_header(old_token)["kid"]
        )
        # Unknown-key bursts must not cause a remote lookup per request.
        parts = new_token.split(".")
        for index in range(30):
            header = {"alg": "RS256", "kid": "unknown-" + str(index)}
            encoded = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode()
            reject(verifier, ".".join([encoded, parts[1], parts[2]]))
        assert len(calls) == 1
        print(
            "Warm-key reuse and 30 unknown-key requests used one JWKS fetch; "
            "waiting for refresh cooldown",
            flush=True,
        )
        time.sleep(31)
        verifier.verify(new_token)
        assert len(calls) == 2
        verifier.verify(old_token)
        subprocess.run(
            ["docker", "stop", "--time", "10", "verdant-issue11-keycloak"],
            check=True,
            capture_output=True,
        )
        stopped = True
        verifier.verify(new_token)
        verifier.verify(old_token)
        cold = AccessTokenVerifier(verifier.settings)
        reject(cold, new_token)
        assert len(calls) == 2
        print(
            "Rotation accepted after cooldown; warm cache survived real issuer outage; "
            "cold verifier failed closed",
            flush=True,
        )
    finally:
        if stopped:
            subprocess.run(
                ["docker", "start", "verdant-issue11-keycloak"], check=True, capture_output=True
            )
            for _ in range(60):
                try:
                    if client.get(ISSUER + "/.well-known/openid-configuration").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError("Owned Keycloak fixture did not recover")
        headers = admin_headers(client)
        result = client.delete(ROOT + "/components/" + component, headers=headers)
        result.raise_for_status()
    after = client.get(ROOT + "/users", headers=headers).json()
    assert subjects == {row["username"]: row["id"] for row in after}
    fresh = AccessTokenVerifier(verifier.settings)
    fresh.verify(service_token(client))
    print(
        "Keycloak recovered, temporary key removed, user subjects preserved and new token verified",
        flush=True,
    )
