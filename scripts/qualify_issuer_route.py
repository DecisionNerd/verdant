"""Strict issuer proof from host, container and Kubernetes; test-only realm and credentials."""

import argparse
import json
from pathlib import Path

import httpx

from verdant.access_tokens import AccessTokenSettings, AccessTokenVerifier, InvalidAccessToken

ORIGIN = "http://auth.127.0.0.1.sslip.io:18081"
REALM = "verdant-routing-qualification"
ISSUER = ORIGIN + "/realms/" + REALM
CLIENT = "verdant-routing-worker"
SECRET = "verdant-test-only-routing-secret"


def setup():
    fixture = json.loads(Path("tests/fixtures/keycloak/realm.json").read_text())
    fixture["realm"] = REALM
    fixture["attributes"] = {"frontendUrl": ORIGIN}
    fixture["users"] = []
    fixture["clients"] = [fixture["clients"][1]]
    fixture["clients"][0]["clientId"] = CLIENT
    fixture["clients"][0]["secret"] = SECRET
    with httpx.Client(base_url="http://127.0.0.1:18081", timeout=10, trust_env=False) as client:
        result = client.post(
            "/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": "verdant-test-admin",
                "password": "verdant-test-only-admin",
            },
        )
        result.raise_for_status()
        headers = {"authorization": "Bearer " + result.json()["access_token"]}
        existing = client.get("/admin/realms/" + REALM, headers=headers)
        if existing.status_code == 404:
            client.post("/admin/realms", headers=headers, json=fixture).raise_for_status()
        else:
            existing.raise_for_status()
            assert existing.json()["attributes"]["frontendUrl"] == ORIGIN


def proof():
    with httpx.Client(timeout=5, trust_env=False) as client:
        result = client.get(ISSUER + "/.well-known/openid-configuration")
        result.raise_for_status()
        discovery = result.json()
        assert discovery["issuer"] == ISSUER
        for endpoint in ["authorization_endpoint", "token_endpoint", "jwks_uri"]:
            assert discovery[endpoint].startswith(ISSUER + "/")
        result = client.post(
            discovery["token_endpoint"],
            data={
                "grant_type": "client_credentials",
                "client_id": CLIENT,
                "client_secret": SECRET,
            },
        )
        result.raise_for_status()
        token = result.json()["access_token"]
    settings = AccessTokenSettings(
        issuer=ISSUER,
        audience="verdant-api",
        clients=frozenset({CLIENT}),
        allow_insecure_http=True,
    )
    verifier = AccessTokenVerifier(settings)
    fetch = verifier.keys.fetch_data
    calls = []

    def counted_fetch():
        calls.append(True)
        return fetch()

    verifier.keys.fetch_data = counted_fetch
    for _ in range(20):
        verifier.verify(token)
    assert len(calls) == 1
    wrong = AccessTokenVerifier(
        AccessTokenSettings(
            issuer=ISSUER,
            audience="wrong-audience",
            clients=frozenset({CLIENT}),
            allow_insecure_http=True,
        )
    )
    try:
        wrong.verify(token)
    except InvalidAccessToken:
        pass
    else:
        raise AssertionError("Wrong audience was accepted")
    print(
        json.dumps(
            {
                "issuer": ISSUER,
                "validations": 20,
                "jwks_fetches": len(calls),
                "wrong_audience": "denied",
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="store_true")
    if parser.parse_args().setup:
        setup()
    proof()
