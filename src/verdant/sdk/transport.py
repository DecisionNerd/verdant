"""Bounded, credential-isolating HTTP transport for the generated client."""

import hashlib
import hmac
import ipaddress
import json
import re
import time
from collections.abc import Callable, Iterator
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, TypeVar
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from verdant.contracts import CONTRACT_VERSION
from verdant.logging import configure_http_logging
from verdant.storage import MAX_ARTIFACT_BYTES

T = TypeVar("T")
JSON_BYTES = 64 * 1024 * 1024


class SDKError(Exception):
    """Bounded public error without request credentials, signed URLs or provider bodies."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def _origin(value: str, *, allow_insecure_http: bool) -> str:
    parsed = urlsplit(value)
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or parsed.scheme not in {"https", "http"}
    ):
        raise ValueError("Expected a fixed HTTP origin without credentials, path or query")
    if parsed.scheme == "http":
        try:
            local = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            local = parsed.hostname == "localhost"
        if not allow_insecure_http or not local:
            raise ValueError("HTTP is permitted only for explicitly enabled loopback fixtures")
    # Accessing port also rejects malformed port syntax.
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    host = parsed.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    return f"{parsed.scheme}://{host}:{port}"


class Transport:
    def __init__(
        self,
        base_url: str,
        token: str | Callable[[], str],
        *,
        storage_origins: tuple[str, ...] = (),
        allow_insecure_http: bool = False,
        timeout: float = 15,
        transport: httpx.BaseTransport | None = None,
    ):
        configure_http_logging()
        self._origin = _origin(base_url, allow_insecure_http=allow_insecure_http)
        self._storage_origins = frozenset(
            _origin(value, allow_insecure_http=allow_insecure_http) for value in storage_origins
        )
        if not 0 < timeout <= 120:
            raise ValueError("Timeout must be positive and at most 120 seconds")
        self._allow_insecure_http = allow_insecure_http
        self._token = token
        self._http = httpx.Client(
            base_url=self._origin,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        # Independent cookie jar and NO default auth: object providers must never
        # receive the platform bearer token or platform browser cookies.
        self._objects = httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self._http.close()
        self._objects.close()

    def _headers(self):
        token = self._token() if callable(self._token) else self._token
        if not isinstance(token, str) or not token or any(char.isspace() for char in token):
            raise SDKError("A valid current access token is required")
        return {"Authorization": "Bearer " + token, "Accept": "application/json"}

    @staticmethod
    def _segment(value):
        return quote(str(value), safe="")

    @staticmethod
    def _path(value):
        if (
            not isinstance(value, str)
            or not value.startswith("/api/v1/")
            or value.startswith("//")
            or "\\" in value
            or urlsplit(value).query
            or urlsplit(value).fragment
            or any(part in {".", ".."} for part in value.split("/"))
            or "%" in value
        ):
            raise SDKError("Expected a canonical platform API path")
        return value

    @staticmethod
    def _compatibility(response):
        version = response.headers.get("X-Verdant-Contract-Version")
        if version != str(CONTRACT_VERSION):
            raise SDKError("Unsupported or missing platform contract version")

    @staticmethod
    def _error(status, data):
        message = "Platform request failed"
        # Public error detail is part of the platform contract; bound decoding
        # and never interpolate URL/headers or third-party error responses.
        if len(data) <= 16384:
            try:
                detail = json.loads(data).get("detail")
                if isinstance(detail, str):
                    message = detail[:500]
                elif isinstance(detail, list):
                    message = "Command validation failed"
            except (ValueError, AttributeError):
                pass
        return SDKError(
            message,
            status=status,
            retryable=status in {429, 502, 503, 504},
        )

    def _request(self, method: str, path: str, *, params: dict, body, model: type[T]) -> T:
        path = self._path(path)
        query = {
            key: str(value).lower() if isinstance(value, bool) else str(value)
            for key, value in params.items()
            if value is not None
        }
        payload = (
            body.model_dump(mode="json", by_alias=True) if isinstance(body, BaseModel) else body
        )
        # Only idempotent reads retry. A lost POST response is an unknown outcome;
        # callers may explicitly resubmit the SAME domain request key to reconcile.
        for attempt in range(3):
            try:
                with self._http.stream(
                    method, path, params=query, json=payload, headers=self._headers()
                ) as response:
                    self._compatibility(response)
                    data = bytearray()
                    for chunk in response.iter_bytes(65536):
                        if len(data) + len(chunk) > JSON_BYTES:
                            raise SDKError("Platform JSON response exceeds the 64 MiB SDK limit")
                        data.extend(chunk)
                    if response.status_code >= 400:
                        error = self._error(response.status_code, data)
                        if method == "GET" and error.retryable and attempt < 2:
                            time.sleep(min(0.1 * 2**attempt, 1))
                            continue
                        raise error
                    if not 200 <= response.status_code < 300:
                        raise SDKError("Unexpected platform redirect or response")
                    try:
                        return TypeAdapter(model).validate_json(data)
                    except (ValidationError, ValueError, TypeError):
                        raise SDKError(
                            "Platform response does not match the typed contract"
                        ) from None
            except httpx.TransportError:
                if method == "GET" and attempt < 2:
                    time.sleep(0.1 * 2**attempt)
                    continue
                raise SDKError(
                    "Platform transport unavailable; command outcome may be unknown",
                    retryable=method == "GET",
                ) from None
        raise SDKError("Platform read retry budget exhausted")

    def iter_pages(
        self, method: Callable[..., T], *, max_pages: int = 1000, **kwargs
    ) -> Iterator[T]:
        """Yield typed pages with a finite budget and duplicate/cyclic cursor detection."""
        if not 1 <= max_pages <= 10000 or "after" in kwargs:
            raise ValueError("Use a finite page budget and let iter_pages own the cursor")
        after, seen = None, set()
        for _ in range(max_pages):
            page = method(**kwargs, after=after)
            if page.next_after is not None and page.next_after in seen:
                raise SDKError("Platform pagination repeated a cursor")
            yield page
            after = page.next_after
            if after is None:
                return
            seen.add(after)
        raise SDKError("SDK pagination budget exhausted; continue explicitly")

    def download_verified(self, descriptor, destination: BinaryIO) -> int:
        """Resolve an authenticated application path and verify before copying bytes.

        Storage origins are supplied by the operator; endpoints and signed URLs
        are never canonical artifact identity. Only a single allowlisted redirect
        is followed, with an independent client carrying no platform credentials.
        """
        if hasattr(descriptor, "artifact"):
            identity = descriptor.artifact
        else:
            identity = descriptor
        digest, expected_size = identity.sha256, identity.size_bytes
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or type(expected_size) is not int
            or not 0 <= expected_size <= MAX_ARTIFACT_BYTES
        ):
            raise SDKError("Invalid artifact identity or size")
        path = self._path(descriptor.download_path)
        try:
            with self._http.stream("GET", path, headers=self._headers()) as response:
                self._compatibility(response)
                if response.status_code in {302, 303, 307, 308}:
                    location = response.headers.get("location", "")
                    parsed = urlsplit(location)
                    origin = _origin(
                        f"{parsed.scheme}://{parsed.netloc}",
                        allow_insecure_http=self._allow_insecure_http,
                    )
                    if parsed.fragment or origin not in self._storage_origins:
                        raise SDKError("Artifact redirect origin is not configured")
                    with self._objects.stream("GET", location) as objects:
                        if objects.status_code != 200:
                            raise SDKError("Artifact provider is unavailable")
                        return self._copy_verified(objects, destination, digest, expected_size)
                if response.status_code != 200:
                    raise SDKError(
                        "Artifact download denied or unavailable", status=response.status_code
                    )
                return self._copy_verified(response, destination, digest, expected_size)
        except (httpx.TransportError, ValueError):
            raise SDKError("Artifact transport or location unavailable") from None

    @staticmethod
    def _copy_verified(response, destination, digest, expected_size):
        count, hasher = 0, hashlib.sha256()
        with SpooledTemporaryFile(max_size=1024 * 1024) as staged:
            for chunk in response.iter_bytes(65536):
                count += len(chunk)
                if count > expected_size:
                    raise SDKError("Artifact byte count exceeds its immutable identity")
                hasher.update(chunk)
                staged.write(chunk)
            if count != expected_size or not hmac.compare_digest(hasher.hexdigest(), digest):
                raise SDKError("Artifact bytes failed immutable size or digest verification")
            staged.seek(0)
            while chunk := staged.read(65536):
                offset = 0
                while offset < len(chunk):
                    accepted = destination.write(chunk[offset:])
                    if type(accepted) is not int or not 0 < accepted <= len(chunk) - offset:
                        raise SDKError("Destination did not accept verified bytes")
                    offset += accepted
        return count
