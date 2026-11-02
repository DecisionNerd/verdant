"""Transport compatibility and truthful discovery of delivered domain authority."""

from typing import Literal

from verdant.contracts import CONTRACT_VERSION, EVENT_VERSION, MANIFEST_VERSION, Contract


class PlatformCapabilities(Contract):
    contract_version: int = CONTRACT_VERSION
    event_version: int = EVENT_VERSION
    manifest_version: int = MANIFEST_VERSION
    identity: Literal["keycloak-issuer-subject"] = "keycloak-issuer-subject"
    transports: tuple[str, ...] = ("browser", "http-bearer", "mcp", "managed")
    domains: tuple[str, ...] = (
        "dataset",
        "candidate",
        "quality",
        "version",
        "operation",
        "process_flow",
        "model_profile",
    )
    experiments: Literal["not_implemented"] = "not_implemented"
    max_page_size: int = 500
    max_artifact_bytes: int = 256 * 1024 * 1024


def capabilities() -> PlatformCapabilities:
    """No namespace rows, counts, runtime paths or credentials are disclosed."""
    return PlatformCapabilities()
