from time import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from verdant.access_tokens import AccessTokenSettings, AccessTokenVerifier, InvalidAccessToken


@pytest.fixture
def tokens(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = AccessTokenSettings(
        issuer="https://auth.example.test/realms/verdant",
        audience="verdant-api",
        clients=frozenset({"worker"}),
    )
    verifier = AccessTokenVerifier(settings)
    monkeypatch.setattr(
        verifier.keys,
        "get_signing_key_from_jwt",
        lambda token: SimpleNamespace(key=key.public_key()),
    )
    claims = {
        "iss": settings.issuer,
        "sub": "stable-subject",
        "aud": settings.audience,
        "exp": int(time()) + 120,
        "iat": int(time()),
        "azp": "worker",
        "typ": "Bearer",
        "scope": "openid verdant",
    }

    def encode(changes=None, omit=()):
        body = claims | (changes or {})
        for name in omit:
            body.pop(name, None)
        return jwt.encode(body, key, algorithm="RS256", headers={"kid": "qualified-key"})

    return verifier, encode


def test_qualified_access_token(tokens):
    verifier, encode = tokens
    assert verifier.verify(encode())["sub"] == "stable-subject"


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://other.example.test/realms/verdant"},
        {"aud": "other-api"},
        {"exp": int(time()) - 60},
        {"iat": int(time()) + 600},
        {"nbf": int(time()) + 600},
        {"typ": "ID"},
        {"azp": "unapproved-client"},
        {"scope": "verdant-admin"},
        {"scope": ["verdant"]},
        {"sub": ""},
        {"iat": True},
        {"exp": "9999999999"},
        {"azp": ["worker"]},
    ],
)
def test_reject_wrong_access_token_contract(tokens, changes):
    verifier, encode = tokens
    with pytest.raises(InvalidAccessToken):
        verifier.verify(encode(changes))


@pytest.mark.parametrize("missing", ["iss", "sub", "aud", "exp", "iat", "azp", "typ", "scope"])
def test_reject_missing_access_claim(tokens, missing):
    verifier, encode = tokens
    with pytest.raises(InvalidAccessToken):
        verifier.verify(encode(omit=[missing]))


def test_reject_algorithm_confusion_before_key_network(tokens, monkeypatch):
    verifier, _ = tokens

    def network_must_not_run(token):
        pytest.fail("Untrusted algorithm caused a JWKS request")

    monkeypatch.setattr(verifier.keys, "get_signing_key_from_jwt", network_must_not_run)
    token = jwt.encode(
        {"sub": "attacker"},
        "test-secret-that-is-at-least-32-bytes",
        algorithm="HS256",
        headers={"kid": "x"},
    )
    with pytest.raises(InvalidAccessToken):
        verifier.verify(token)


def test_fixed_issuer_transport_configuration():
    with pytest.raises(ValueError):
        AccessTokenSettings(
            issuer="http://auth.example.test/realms/v", audience="api", clients=frozenset({"x"})
        )
    AccessTokenSettings(
        issuer="http://auth.verdant.test/realms/v",
        audience="api",
        clients=frozenset({"x"}),
        allow_insecure_http=True,
    )
