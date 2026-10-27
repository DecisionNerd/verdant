"""Seed the owner/denied-resource browser fixture without changing existing subjects."""

import os
from uuid import UUID

import httpx
from sqlalchemy import select

from verdant.application import RegistryService
from verdant.contracts import DatasetCreate
from verdant.identity import IdentityService
from verdant.persistence import DatasetRow, NamespaceRow, make_sessions

ISSUER = "http://127.0.0.1:18081/realms/verdant-qualification"
namespace = UUID("83fe7989-6127-4613-bd3d-9f8c20df8bfb")
url = os.environ["VERDANT_DATABASE_URL"]
if not url.endswith("/verdant_issue11_workspace"):
    raise RuntimeError("Use only the isolated issue-11 workspace database")
with httpx.Client(base_url="http://127.0.0.1:18081", timeout=5) as client:
    response = client.post(
        "/realms/master/protocol/openid-connect/token",
        data={
            "client_id": "admin-cli",
            "grant_type": "password",
            "username": "verdant-test-admin",
            "password": "verdant-test-only-admin",
        },
    )
    response.raise_for_status()
    client.headers["authorization"] = "Bearer " + response.json()["access_token"]
    root = "/admin/realms/verdant-qualification"
    response = client.get(root + "/users", params={"username": "owner", "exact": "true"})
    response.raise_for_status()
    owner_subject = response.json()[0]["id"]
    response = client.get(root + "/users", params={"username": "no-grants", "exact": "true"})
    response.raise_for_status()
    if not response.json():
        client.post(
            root + "/users",
            json={
                "username": "no-grants",
                "enabled": True,
                "firstName": "Nora",
                "lastName": "No Grants",
                "email": "no-grants@example.test",
                "emailVerified": True,
                "groups": ["/workspace"],
                "credentials": [
                    {"type": "password", "value": "verdant-test-only-no-grants", "temporary": False}
                ],
            },
        ).raise_for_status()
sessions = make_sessions(url)
identities = IdentityService(sessions)
actor = identities.resolve(ISSUER, owner_subject, kind="human")
with sessions.begin() as session:
    if session.get(NamespaceRow, namespace) is None:
        session.add(NamespaceRow(id=namespace, name="Browser permissions proof"))
identities.grant(actor.id, namespace, "write")
with sessions() as session:
    existing = session.scalar(select(DatasetRow).where(DatasetRow.namespace_id == namespace))
if existing is None:
    dataset = RegistryService(sessions).create_dataset(
        actor,
        namespace,
        DatasetCreate(
            slug="owner-browser",
            name="Owner browser proof",
            modality="document",
        ),
    )
else:
    dataset = existing
print(f"/api/v1/namespaces/{namespace}/datasets/{dataset.id}")
print("owner_id=" + str(actor.id))
