#!/usr/bin/env python3
"""Create stable workflow-only credentials without changing existing platform inputs."""

import os
import secrets
from pathlib import Path


def bootstrap():
    directory = Path(__file__).resolve().parent / ".local"
    if not (directory / "compose.env").is_file():
        raise SystemExit("Run platform bootstrap first")
    target = directory / "workflows.env"
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print("Existing workflow credentials retained.")
        return
    with os.fdopen(fd, "w") as stream:
        stream.write(f"WORKFLOW_DB_PASSWORD={secrets.token_hex(32)}\n")
        stream.write(f"WORKFLOW_API_AUTH=verdant:{secrets.token_hex(32)}\n")
    print("Created private workflow credentials. Preserve with the workflow database volume.")


if __name__ == "__main__":
    bootstrap()
