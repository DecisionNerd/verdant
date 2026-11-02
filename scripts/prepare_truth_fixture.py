"""Prepare an owned, disposable HTTPS truth qualification fixture without touching live stacks.

Run through `uv run python scripts/prepare_truth_fixture.py`. Generated credentials,
certificates and runtime configuration stay under ignored e2e/truth/.local (0700).
"""

import copy
import ipaddress
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import certifi
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "apps/workspace/e2e/truth/.local"
ISSUER = "https://identity.localhost:19243/realms/verdant-truth"
APP = "https://workspace.localhost:19243"
LABELS = "https://labels.localhost:19243"
MACHINE = "https://machine.localhost:19243"
IMAGE = (
    "localhost:5001/label-studio-oidc@sha256:"
    "d7f8aac5ddee20155a967d91927d7278d0ed5f0f99ccbf9fdb4818cf653a1cea"
)
PROJECT = "verdant-truth-bc0aaa6b"


def write(name, content, mode=0o600):
    path = DIRECTORY / name
    path.write_text(content)
    path.chmod(mode)


def certificates():
    directory = DIRECTORY / "certs"
    directory.mkdir(mode=0o700, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Verdant isolated truth CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "*.localhost")]))
        .issuer_name(subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=14))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("identity.localhost"),
                    x509.DNSName("workspace.localhost"),
                    x509.DNSName("labels.localhost"),
                    x509.DNSName("machine.localhost"),
                    x509.DNSName("objects.localhost"),
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    ca_pem = ca.public_bytes(serialization.Encoding.PEM).decode()
    write("certs/ca.pem", ca_pem, 0o644)
    write("certs/cert.pem", cert.public_bytes(serialization.Encoding.PEM).decode() + ca_pem, 0o644)
    write(
        "certs/key.pem",
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )
    write("trust.pem", Path(certifi.where()).read_text() + ca_pem, 0o644)


def main():
    os.umask(0o077)
    DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    DIRECTORY.chmod(0o700)
    if (DIRECTORY / "environment.json").exists():
        print("Prepared truth fixture already exists; preserving its identities and secrets.")
        return
    certificates()
    credentials = {name: secrets.token_urlsafe(24) for name in ("owner", "editor")}
    db_password, browser_secret, native_secret, storage_secret = [
        secrets.token_urlsafe(32) for _ in range(4)
    ]
    realm = json.loads((ROOT / "apps/workspace/e2e/realm.json").read_text())
    realm.update(realm="verdant-truth", sslRequired="all")
    browser = realm["clients"][0]
    browser.update(secret=browser_secret, redirectUris=[APP + "/auth/callback"], webOrigins=[APP])
    browser["attributes"]["post.logout.redirect.uris"] = APP + "/"
    native = copy.deepcopy(browser)
    native.update(
        clientId="label-studio",
        name="Isolated native Label Studio",
        secret=native_secret,
        redirectUris=[LABELS + "/oidc/callback/"],
        webOrigins=[],
    )
    native["attributes"]["post.logout.redirect.uris"] = LABELS + "/oidc/logged-out/"
    realm["clients"].append(native)
    for user in realm["users"]:
        user["credentials"][0]["value"] = credentials[user["username"]]
    write("realm.json", json.dumps(realm, indent=2) + "\n", 0o644)
    write("credentials.json", json.dumps(credentials) + "\n")
    write("compose.env", f"FIXTURE_DB_PASSWORD={db_password}\n")
    native_env = {
        "DJANGO_DB": "postgresql",
        "POSTGRE_HOST": "native-db",
        "POSTGRE_NAME": "labeling",
        "POSTGRE_USER": "labeling",
        "POSTGRE_PASSWORD": db_password,
        "LS_OIDC_ISSUER": ISSUER,
        "LS_OIDC_CLIENT_ID": "label-studio",
        "LS_OIDC_CLIENT_SECRET": native_secret,
        "LS_OIDC_GROUP": "/workspace",
        "LS_OIDC_ORGANIZATION_ID": "1",
        "LS_OIDC_ORIGIN": LABELS,
        "LS_MACHINE_ORIGIN": MACHINE,
        "REQUESTS_CA_BUNDLE": "/trust.pem",
        "LOG_LEVEL": "WARNING",
        "SECRET_KEY": secrets.token_urlsafe(48),
        "LS_OIDC_LEASE_SECONDS": "300",
    }
    write("native.env", "".join(f"{name}={value}\n" for name, value in native_env.items()))
    write(
        "objects.json",
        json.dumps(
            {
                "identities": [
                    {
                        "name": "truth-fixture",
                        "credentials": [
                            {"accessKey": "truth-fixture", "secretKey": storage_secret}
                        ],
                        "actions": ["Admin", "Read", "List", "Tagging", "Write"],
                    }
                ]
            }
        )
        + "\n",
        0o644,
    )
    environment = {
        "COMPOSE_PROJECT_NAME": PROJECT,
        "VERDANT_DATABASE_URL": f"postgresql+psycopg://postgres:{db_password}@127.0.0.1:19232/verdant_truth_test",
        "VERDANT_TEST_DATABASE_URL": f"postgresql+psycopg://postgres:{db_password}@127.0.0.1:19232/verdant_truth_test",
        "VERDANT_OIDC_ISSUER": ISSUER,
        "VERDANT_OIDC_CLIENT_ID": "verdant-browser",
        "VERDANT_OIDC_CLIENT_SECRET": browser_secret,
        "VERDANT_APP_URL": APP,
        "VERDANT_OIDC_GROUP": "/workspace",
        "VERDANT_API_AUDIENCE": "verdant-api",
        "VERDANT_SERVICE_CLIENTS": "verdant-worker",
        "VERDANT_INSECURE_LOCAL_HTTP": "0",
        "VERDANT_S3_ENDPOINT": "https://objects.localhost:19243",
        "VERDANT_S3_BROWSER_ENDPOINT": "https://objects.localhost:19243",
        "VERDANT_S3_BUCKET": "verdant-artifacts",
        "VERDANT_S3_ACCESS_KEY": "truth-fixture",
        "VERDANT_S3_SECRET_KEY": storage_secret,
        "VERDANT_STORAGE_TEST_ENDPOINT": "https://objects.localhost:19243",
        "VERDANT_STORAGE_TEST_BROWSER_ENDPOINT": "https://objects.localhost:19243",
        "VERDANT_STORAGE_TEST_BUCKET": "verdant-artifacts",
        "VERDANT_STORAGE_TEST_LOCAL_HTTP": "0",
        "AWS_ACCESS_KEY_ID": "truth-fixture",
        "AWS_SECRET_ACCESS_KEY": storage_secret,
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_CA_BUNDLE": str(DIRECTORY / "trust.pem"),
        "SSL_CERT_FILE": str(DIRECTORY / "trust.pem"),
        "REQUESTS_CA_BUNDLE": str(DIRECTORY / "trust.pem"),
        "VERDANT_LS_BROWSER_ORIGIN": LABELS,
        "VERDANT_LS_MACHINE_ORIGIN": MACHINE,
        "VERDANT_LS_IMAGE": IMAGE,
        "VERDANT_LS_CA_BUNDLE": str(DIRECTORY / "trust.pem"),
        "VERDANT_LS_TLS_CA": str(DIRECTORY / "trust.pem"),
    }
    write("environment.json", json.dumps(environment, indent=2) + "\n")
    print("Prepared private isolated truth fixture; credentials were not printed.")


if __name__ == "__main__":
    main()
