---
title: Python SDK and clients
description: Typed, authorized dataset access with verified artifact consumption.
---

`verdant.sdk.Client` ships with the Python platform checkout. Its generated methods
use the same Pydantic contracts and current namespace authorization as the workspace.
Install the locked checkout with `uv sync --locked --group workflows`; the SDK is
not claimed as a separately published package.

Provide a current Keycloak access token from an explicitly configured human OAuth
or service client. The issuer, audience, `verdant` scope, exact subject, principal
kind and current grants are checked by both HTTP and MCP. The client accepts a
callback for refreshed tokens. Never pass a human password as an API credential.

The runnable exact-version example is:

```sh
uv run python examples/sdk_consume.py NAMESPACE_UUID DATASET_UUID VERSION_UUID
```

It reads `VERDANT_API_URL`, `VERDANT_ACCESS_TOKEN`, and optional comma-separated
`VERDANT_SDK_STORAGE_ORIGINS`. Set those privately. It prints only result identities.
`verified_manifest(...)` verifies descriptor/version identity, bytes, supported schema
and frozen membership. `download_verified(...)` streams larger artifacts through a
bounded spool and verifies exact size/digest before writing output. Storage origins
are separate configuration; signed URLs and local paths are not canonical identity.
The object client receives no platform token or cookies.

Generated methods include datasets, samples/drafts, candidates, quality, versions,
operations, process/model profiles, annotation/review and truth contracts.
`iter_pages(...)` detects cyclic cursors; `wait_operation(...)` has a finite budget
and returns paused or ambiguous states without claiming success. Only reads retry
automatically. A lost command response requires reconciliation using the same domain
request key; a fresh trial uses a new identity.

[SDK limits, regeneration and examples](https://github.com/DecisionNerd/verdant/blob/main/docs/engineering/PYTHON-SDK.md)
include the 16 MiB typed-manifest reader, 256 MiB verified streaming limit and request
deadlines. CI checks the generated client against the public OpenAPI snapshot.
Experiment-engine authority remains explicitly `not_implemented` in capabilities;
the SDK does not manufacture experiment outcomes. Earlier digestion CLI commands
are separate legacy tools.
