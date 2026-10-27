"""Keycloak access tokens: fixed trust configuration and cached issuer keys."""

from dataclasses import dataclass
from urllib.parse import urlsplit

import jwt
from jwt import PyJWKClient
from starlette.concurrency import run_in_threadpool


class InvalidAccessToken(Exception):
    """Safe authentication failure; never return provider/token details."""


@dataclass(frozen=True)
class AccessTokenSettings:
    issuer: str
    audience: str
    clients: frozenset[str]
    required_scope: str = "verdant"
    allow_insecure_http: bool = False

    def __post_init__(self):
        parsed = urlsplit(self.issuer)
        if (
            parsed.scheme not in ({"https", "http"} if self.allow_insecure_http else {"https"})
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or self.issuer.endswith("/")
            or not self.audience
            or not self.clients
            or any(not client for client in self.clients)
            or not self.required_scope
            or any(char.isspace() for char in self.required_scope)
        ):
            raise ValueError("Invalid fixed Keycloak access-token configuration")


class AccessTokenVerifier:
    def __init__(self, settings: AccessTokenSettings):
        self.settings = settings
        self.keys = PyJWKClient(
            settings.issuer + "/protocol/openid-connect/certs",
            cache_keys=False,
            cache_jwk_set=True,
            lifespan=300,
            timeout=3,
            cooldown_duration=30,
        )

    def verify(self, token: str) -> dict:
        try:
            # Bound parsing work and reject unsupported algorithms before any key retrieval.
            if not token or len(token) > 16384:
                raise InvalidAccessToken()
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise InvalidAccessToken()
            key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                leeway=10,
                options={"require": ["iss", "sub", "aud", "exp", "iat", "azp", "typ"]},
            )
            if (
                not isinstance(claims["sub"], str)
                or not claims["sub"]
                or claims["typ"] != "Bearer"
                or claims["azp"] not in self.settings.clients
                or not isinstance(claims.get("scope"), str)
                or self.settings.required_scope not in claims["scope"].split()
                or any(type(claims[name]) is not int for name in ("exp", "iat"))
                or ("nbf" in claims and type(claims["nbf"]) is not int)
                or claims["iat"] >= claims["exp"]
            ):
                raise InvalidAccessToken()
            return claims
        except (jwt.PyJWTError, TypeError, ValueError, KeyError):
            raise InvalidAccessToken() from None

    async def verify_async(self, token: str) -> dict:
        # PyJWKClient is synchronous; warm validation and refresh both stay off the event loop.
        return await run_in_threadpool(self.verify, token)
