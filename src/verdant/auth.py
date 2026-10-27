"""Backend-owned Keycloak login using Authlib and PostgreSQL sessions."""

import os
import secrets
from dataclasses import dataclass, field
from time import time
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starsessions import (
    SessionMiddleware,
    get_session_id,
    load_session,
    regenerate_session_id,
)

from verdant.access_tokens import AccessTokenSettings, AccessTokenVerifier, InvalidAccessToken
from verdant.browser_sessions import DatabaseSessionStore
from verdant.domain import Forbidden, Principal
from verdant.identity import IdentityService, current_principal
from verdant.persistence import PrincipalRow
from verdant.telemetry import auth_failure, observed


@dataclass(frozen=True)
class IdentitySettings:
    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    app_url: str
    admission_group: str
    audience: str
    service_clients: frozenset[str]
    allow_insecure_http: bool = False

    def __post_init__(self):
        AccessTokenSettings(
            self.issuer,
            self.audience,
            self.service_clients,
            allow_insecure_http=self.allow_insecure_http,
        )
        parsed = urlsplit(self.app_url)
        if (
            parsed.scheme not in ({"https", "http"} if self.allow_insecure_http else {"https"})
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or self.app_url.endswith("/")
            or not self.client_id
            or not self.client_secret
            or not self.admission_group
        ):
            raise ValueError("Invalid fixed browser identity configuration")

    @classmethod
    def from_env(cls):
        if not os.environ.get("VERDANT_OIDC_ISSUER"):
            return None
        return cls(
            issuer=os.environ["VERDANT_OIDC_ISSUER"],
            client_id=os.environ["VERDANT_OIDC_CLIENT_ID"],
            client_secret=os.environ["VERDANT_OIDC_CLIENT_SECRET"],
            app_url=os.environ["VERDANT_APP_URL"],
            admission_group=os.environ["VERDANT_OIDC_GROUP"],
            audience=os.environ["VERDANT_API_AUDIENCE"],
            service_clients=frozenset(os.environ["VERDANT_SERVICE_CLIENTS"].split(",")),
            allow_insecure_http=os.environ.get("VERDANT_INSECURE_LOCAL_HTTP") == "1",
        )


class IdentityRuntime:
    def __init__(self, sessions: sessionmaker[Session], settings: IdentitySettings):
        self.sessions = sessions
        self.settings = settings
        self.store = DatabaseSessionStore(sessions)
        self.identities = IdentityService(sessions)
        self.tokens = AccessTokenVerifier(
            AccessTokenSettings(
                settings.issuer,
                settings.audience,
                settings.service_clients,
                allow_insecure_http=settings.allow_insecure_http,
            )
        )
        oauth = OAuth()
        self.oidc = oauth.register(
            "keycloak",
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            server_metadata_url=settings.issuer + "/.well-known/openid-configuration",
            client_kwargs={
                "scope": "openid profile email",
                "code_challenge_method": "S256",
                "timeout": 3,
            },
        )

    async def metadata(self):
        metadata = await self.oidc.load_server_metadata()
        if metadata.get("issuer") != self.settings.issuer:
            raise ValueError("Identity issuer metadata mismatch")
        # Pin the qualified algorithm even if discovery advertises other algorithms.
        metadata["id_token_signing_alg_values_supported"] = ["RS256"]
        return metadata

    def _current(self, principal_id: UUID, generation: int) -> Principal:
        with self.sessions() as session:
            row = session.get(PrincipalRow, principal_id)
            if row is None or row.session_generation != generation:
                raise Forbidden("Session revoked")
            return current_principal(session, principal_id)

    async def browser_principal(self, request: Request) -> Principal:
        await load_session(request)
        try:
            if request.session.get("auth_expires", 0) <= time():
                raise ValueError("expired")
            return await run_in_threadpool(
                self._current, UUID(request.session["principal_id"]), request.session["generation"]
            )
        except (KeyError, TypeError, ValueError, Forbidden):
            request.session.clear()
            raise HTTPException(401, "Sign in required") from None

    @observed("auth")
    async def principal(self, request: Request) -> Principal:
        authorization = request.headers.get("authorization")
        if authorization is not None:
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not token:
                raise HTTPException(401, "Valid access token required")
            try:
                claims = await self.tokens.verify_async(token)
                return await run_in_threadpool(
                    self.identities.resolve, claims["iss"], claims["sub"], kind="service"
                )
            except InvalidAccessToken:
                raise HTTPException(401, "Valid access token required") from None
        actor = await self.browser_principal(request)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            self.csrf(request)
        return actor

    def csrf(self, request: Request):
        supplied = request.headers.get("x-csrf-token", "")
        expected = request.session.get("csrf", "")
        if (
            request.headers.get("origin") != self.settings.app_url
            or not supplied
            or not expected
            or not secrets.compare_digest(supplied.encode(), expected.encode())
        ):
            raise HTTPException(403, "Request origin or CSRF token denied")

    def failure(self, request: Request, status: int, code: str, message: str):
        auth_failure(code)
        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(
                self.settings.app_url + "/?" + urlencode({"auth_error": code}), status_code=303
            )
        raise HTTPException(status, message)

    async def rotate(self, request: Request):
        old = get_session_id(request)
        if old:
            await self.store.remove(old)
        return regenerate_session_id(request)

    def install(self, app, principal_dependency):
        app.add_middleware(
            SessionMiddleware,
            store=self.store,
            lifetime=1800,
            rolling=False,
            cookie_name="verdant_session",
            cookie_path="/",
            cookie_same_site="lax",
            cookie_https_only=not self.settings.allow_insecure_http,
        )
        app.dependency_overrides[principal_dependency] = self.principal
        app.state.identity = self
        router = APIRouter(prefix="/auth")

        @router.get("/login")
        @observed("auth")
        async def login(request: Request):
            if request.query_params.get("next", "/") != "/":
                raise HTTPException(400, "Return target denied")
            await load_session(request)
            request.session.clear()
            session_id = await self.rotate(request)
            try:
                await self.metadata()
                options = (
                    {"prompt": "login"} if request.query_params.get("switch_account") == "1" else {}
                )
                response = await self.oidc.authorize_redirect(
                    request,
                    self.settings.app_url + "/auth/callback",
                    **options,
                )
                state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
                await run_in_threadpool(self.store.start_attempt, state, session_id)
                return response
            except Exception:
                request.session.clear()
                return self.failure(
                    request, 503, "unavailable", "Identity provider unavailable; retry sign in"
                )

        @router.get("/callback")
        @observed("auth")
        async def callback(request: Request):
            await load_session(request)
            state, session_id = request.query_params.get("state", ""), get_session_id(request)
            if not session_id or not await run_in_threadpool(
                self.store.consume_attempt, state, session_id
            ):
                return self.failure(
                    request, 400, "expired", "Login expired or already used; start a new sign in"
                )
            try:
                await self.metadata()
                token = await self.oidc.authorize_access_token(
                    request,
                    leeway=10,
                    claims_options={
                        "iss": {"essential": True, "value": self.settings.issuer},
                        "aud": {"essential": True, "value": self.settings.client_id},
                    },
                )
                claims = token.get("userinfo")
                if not token.get("id_token") or not claims:
                    raise ValueError("ID token missing")
                if (
                    not isinstance(claims.get("sub"), str)
                    or not claims["sub"]
                    or not isinstance(claims.get("groups"), list)
                    or self.settings.admission_group not in claims["groups"]
                ):
                    raise Forbidden("Workspace admission denied")
                email = claims.get("email")
                if not isinstance(email, str) or len(email) > 320:
                    email = None
                if not await run_in_threadpool(self.store.finalize_attempt, session_id):
                    raise ValueError("Login superseded or revoked")
                principal_id = await run_in_threadpool(
                    self.identities.provision,
                    self.settings.issuer,
                    claims["sub"],
                    kind="human",
                    email=email,
                    actor_id=None,
                )
                generation = await run_in_threadpool(
                    self.identities.session_generation, principal_id
                )
                auth_expires = min(int(time()) + 1800, int(claims["exp"]))
                if auth_expires <= time():
                    raise ValueError("Expired identity")
            except Forbidden:
                request.session.clear()
                return self.failure(request, 403, "denied", "Workspace admission denied")
            except Exception:
                request.session.clear()
                return self.failure(request, 400, "failed", "Sign in failed; start a new sign in")
            request.session.clear()
            await self.rotate(request)
            request.session.update(
                {
                    "principal_id": str(principal_id),
                    "generation": generation,
                    "auth_expires": auth_expires,
                    "csrf": secrets.token_urlsafe(32),
                    "display_name": str(claims.get("name") or email or "Workspace member")[:200],
                }
            )
            return RedirectResponse(self.settings.app_url + "/", status_code=303)

        @router.get("/session")
        async def session_info(request: Request):
            try:
                actor = await self.browser_principal(request)
            except HTTPException as exc:
                if exc.status_code != 401:
                    raise
                return {"authenticated": False}
            return {
                "authenticated": True,
                "display_name": request.session.get("display_name", "Workspace member"),
                "principal_id": str(actor.id),
                "csrf_token": request.session["csrf"],
                "expires_at": request.session["auth_expires"],
            }

        @router.post("/logout")
        @observed("auth")
        async def logout(request: Request):
            await load_session(request)
            self.csrf(request)
            request.session.clear()
            # Destroy locally even if Keycloak is unavailable. Its SSO session is separate.
            old = get_session_id(request)
            if old:
                await self.store.remove(old)
            try:
                metadata = await self.metadata()
                target = (
                    metadata["end_session_endpoint"]
                    + "?"
                    + urlencode(
                        {
                            "client_id": self.settings.client_id,
                            "post_logout_redirect_uri": self.settings.app_url + "/?signed_out=1",
                        }
                    )
                )
            except Exception:
                target = self.settings.app_url + "/?signed_out=1&identity_unavailable=1"
            return {"logout_url": target}

        app.include_router(router)

        @app.middleware("http")
        async def private_responses(request, call_next):
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response

        from verdant.mcp_server import create_mcp_app

        mcp_app = create_mcp_app(self)
        app.router.lifespan_context = mcp_app.lifespan
        app.mount("/mcp", mcp_app)
