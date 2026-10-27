#!/usr/bin/env python3
"""Generate private, stable local inputs. Existing configurations are never reset."""

import json
import os
import secrets
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DESTINATION = ROOT / ".local"
REQUIRED = {"compose.env", "realm.json", "objects.json", "database.sql"}


def bootstrap():
    if DESTINATION.exists():
        missing = REQUIRED - {p.name for p in DESTINATION.iterdir()}
        if missing:
            raise SystemExit(
                f"Incomplete local configuration; recover missing files: {sorted(missing)}"
            )
        print("Existing local configuration retained.")
        return
    directory = Path(tempfile.mkdtemp(prefix=".bootstrap-", dir=ROOT))
    try:
        values = {
            name: secrets.token_hex(32)
            for name in (
                "POSTGRES_PASSWORD",
                "REGISTRY_PASSWORD",
                "KEYCLOAK_PASSWORD",
                "KEYCLOAK_ADMIN_PASSWORD",
                "BROWSER_SECRET",
                "WORKER_SECRET",
                "OBJECT_ADMIN_SECRET",
                "OBJECT_REGISTRY_SECRET",
            )
        }
        values.update(
            VERDANT_APP_URL="http://127.0.0.1:18800",
            VERDANT_OIDC_ISSUER="http://auth.127.0.0.1.sslip.io:18801/realms/verdant-local",
        )
        environment = directory / "compose.env"
        environment.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
        environment.chmod(0o600)
        realm = json.loads((ROOT / "realm-template.json").read_text())
        for client in realm["clients"]:
            browser = client["clientId"] == "verdant-browser"
            client["secret"] = values["BROWSER_SECRET" if browser else "WORKER_SECRET"]
            if browser:
                origin = values["VERDANT_APP_URL"]
                client.update(redirectUris=[origin + "/auth/callback"], webOrigins=[origin])
                client["attributes"]["post.logout.redirect.uris"] = origin + "/?signed_out=1"
        (directory / "realm.json").write_text(json.dumps(realm, indent=2) + "\n")
        identities = [
            {
                "name": "bootstrap",
                "credentials": [
                    {"accessKey": "verdant-local-admin", "secretKey": values["OBJECT_ADMIN_SECRET"]}
                ],
                "actions": ["Admin"],
            },
            {
                "name": "registry",
                "credentials": [
                    {
                        "accessKey": "verdant-local-registry",
                        "secretKey": values["OBJECT_REGISTRY_SECRET"],
                    }
                ],
                "actions": [
                    "Read:verdant-artifacts",
                    "Write:verdant-artifacts",
                    "List:verdant-artifacts",
                ],
            },
        ]
        (directory / "objects.json").write_text(
            json.dumps({"identities": identities}, indent=2) + "\n"
        )
        # Passwords are generated hexadecimal; no user text enters SQL literals.
        (directory / "database.sql").write_text(
            f"CREATE USER registry PASSWORD '{values['REGISTRY_PASSWORD']}';\n"
            "CREATE DATABASE registry OWNER registry;\n"
            f"CREATE USER keycloak PASSWORD '{values['KEYCLOAK_PASSWORD']}';\n"
            "CREATE DATABASE keycloak OWNER keycloak;\n"
        )
        os.rename(directory, DESTINATION)
    finally:
        if directory.exists():
            for item in directory.iterdir():
                item.unlink()
            directory.rmdir()
    print("Created private local configuration. Keep .local with the persisted volumes.")


if __name__ == "__main__":
    bootstrap()
