"""SDK/REST/MCP authority with real PostgreSQL/S3 and signed fixture JWTs.

JWKS delivery is the only identity double; browser/Keycloak acceptance is separately
covered by the real interactive regression. No domain service or object bytes mocked.
"""

import hashlib
import io
from dataclasses import replace
from time import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select

from verdant.api import create_app
from verdant.auth import IdentitySettings
from verdant.contracts import DatasetCreate, MembershipWrite
from verdant.identity import IdentityService
from verdant.ingestion import UploadDeclaration, UploadPartRequest, UploadRequest
from verdant.ingestion_runner import IngestionRunner
from verdant.operations import OperationAction
from verdant.persistence import AuditRow
from verdant.publication import EmptyCommand, PublicationBatch, PublicationStart
from verdant.quality import QualityDefinition
from verdant.quality_service import PolicyCreate, ValidationBatch, ValidationStart
from verdant.sdk import Client, SDKError
from verdant.versioning import CandidateCapture, CandidatePrepare


@pytest.fixture(params=["human", "service"])
def sdk_identity(request, sessions, registry, storage, monkeypatch):
    _, _, ns = registry
    adapter, _ = storage
    settings = IdentitySettings(
        issuer="https://sdk-issuer.test/realms/v",
        client_id="browser",
        client_secret="synthetic-test-only",
        app_url="https://sdk.test",
        admission_group="/users",
        audience="verdant-api",
        service_clients=frozenset({"sdk-worker"}),
        human_clients=frozenset({"sdk-human"}),
    )
    app = create_app(sessions, settings, storage=adapter)
    identities = IdentityService(sessions)
    subject = str(uuid4())
    principal_id = identities.provision(settings.issuer, subject, kind=request.param)
    identities.grant(principal_id, ns, "write")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        app.state.identity.tokens.keys,
        "get_signing_key_from_jwt",
        lambda _: SimpleNamespace(key=key.public_key()),
    )
    claims = {
        "iss": settings.issuer,
        "sub": subject,
        "aud": settings.audience,
        "exp": int(time()) + 300,
        "iat": int(time()),
        "typ": "Bearer",
        "scope": "verdant",
        "azp": "sdk-human" if request.param == "human" else "sdk-worker",
    }

    def token(**changes):
        return jwt.encode(claims | changes, key, algorithm="RS256", headers={"kid": "fixture"})

    with TestClient(app, base_url=settings.app_url) as http:
        native = httpx.HTTPTransport()

        def bridge(req):
            if req.url.host != "sdk.test":
                return native.handle_request(req)
            response = http.request(
                req.method,
                str(req.url),
                headers=dict(req.headers),
                content=req.content,
                follow_redirects=False,
            )
            return httpx.Response(
                response.status_code, headers=response.headers, content=response.content
            )

        with Client(
            settings.app_url,
            token,
            storage_origins=(adapter.settings.browser_endpoint,),
            allow_insecure_http=True,
            transport=httpx.MockTransport(bridge),
        ) as sdk:
            yield SimpleNamespace(
                sdk=sdk,
                http=http,
                token=token,
                ns=ns,
                actor=principal_id,
                storage=adapter,
                identities=identities,
                claims=claims,
                kind=request.param,
                app=app,
            )
        native.close()


def tool(fixture, name, arguments):
    headers = {
        "Authorization": "Bearer " + fixture.token(),
        "Accept": "application/json, text/event-stream",
    }
    response = fixture.http.post(
        "/mcp/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def initialize(fixture):
    response = fixture.http.post(
        "/mcp/",
        headers={
            "Authorization": "Bearer " + fixture.token(),
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "sdk-proof", "version": "1"},
            },
        },
    )
    assert response.status_code == 200


def test_typed_sdk_upload_candidate_publish_manifest_and_mcp_parity(sdk_identity, sessions):
    f = sdk_identity
    sdk, ns = f.sdk, f.ns
    assert sdk.platform_capabilities().experiments == "not_implemented"
    dataset = sdk.create_dataset(
        namespace_id=ns,
        command=DatasetCreate(slug="sdk-" + uuid4().hex, name="SDK lifecycle", modality="text"),
    )
    bodies = [b"first verified SDK content", b"second verified SDK content"]
    command = UploadRequest(
        request_key=str(uuid4()),
        items=[
            UploadDeclaration(
                source_key="sdk-" + uuid4().hex,
                modality="text",
                sha256=hashlib.sha256(body).hexdigest(),
                size_bytes=len(body),
                metadata={"answer": "must never enter model input"},
            )
            for body in bodies
        ],
    )
    batch = sdk.admit_upload(namespace_id=ns, command=command)
    assert sdk.admit_upload(namespace_id=ns, command=command).operation.id == batch.operation.id
    for item, body in zip(batch.items, bodies, strict=True):
        args = dict(namespace_id=ns, operation_id=batch.operation.id, item_id=item.id)
        sdk.initiate_upload(**args, command=OperationAction(epoch=1))
        signed = sdk.sign_upload_part(**args, command=UploadPartRequest(epoch=1, part_number=1))
        with httpx.Client(trust_env=False, timeout=10) as http:
            http.put(signed.url, content=body).raise_for_status()
        sdk.complete_upload(**args, command=OperationAction(epoch=1))
    actor = replace(f.identities.resolve(f.claims["iss"], f.claims["sub"]), transport="managed")
    IngestionRunner(sessions, f.storage).run(actor, ns, batch.operation.id, 1, limit=25)
    accepted = sdk.get_ingestion(namespace_id=ns, operation_id=batch.operation.id)
    for index, item in enumerate(accepted.items):
        assert item.state == "accepted"
        sdk.set_membership(
            namespace_id=ns,
            dataset_id=dataset.id,
            sample_id=item.sample_id,
            command=MembershipWrite(
                expected_revision=index, sample_revision_id=item.accepted_revision_id, split="train"
            ),
        )
    candidate = sdk.capture_candidate(
        namespace_id=ns,
        dataset_id=dataset.id,
        command=CandidateCapture(request_key=str(uuid4()), expected_revision=2),
    )
    candidate = sdk.prepare_candidate(
        namespace_id=ns,
        dataset_id=dataset.id,
        candidate_id=candidate.id,
        command=CandidatePrepare(expected_count=0, limit=100),
    )
    policy = sdk.create_quality_policy(
        namespace_id=ns,
        dataset_id=dataset.id,
        command=PolicyCreate(
            request_key=str(uuid4()),
            name="sdk-minimum",
            expected_revision=0,
            definition=QualityDefinition.model_validate(
                {"rules": [{"id": "min", "kind": "minimum_samples", "minimum": 2}]}
            ),
        ),
    )
    report = sdk.start_quality_validation(
        namespace_id=ns,
        dataset_id=dataset.id,
        candidate_id=candidate.id,
        command=ValidationStart(request_key=str(uuid4()), policy_id=policy.id),
    )
    report = sdk.advance_quality_validation(
        namespace_id=ns,
        dataset_id=dataset.id,
        report_id=report.id,
        command=ValidationBatch(expected_count=0, limit=100),
    )
    publication = sdk.start_publication(
        namespace_id=ns,
        dataset_id=dataset.id,
        candidate_id=candidate.id,
        command=PublicationStart(request_key=str(uuid4()), report_id=report.id, label="sdk-v1"),
    )
    publication = sdk.prepare_publication(
        namespace_id=ns,
        dataset_id=dataset.id,
        publication_id=publication.id,
        command=PublicationBatch(expected_count=0, limit=25),
    )
    publication = sdk.finalize_publication(
        namespace_id=ns,
        dataset_id=dataset.id,
        publication_id=publication.id,
        command=EmptyCommand(),
    )
    args = dict(namespace_id=ns, dataset_id=dataset.id, version_id=publication.version_id)
    manifest = sdk.verified_manifest(**args)
    assert manifest.generation_id == candidate.generation_id and len(manifest.members) == 2
    pages = list(sdk.iter_pages(sdk.version_members, **args, limit=1))
    assert len(pages) == 2
    first = pages[0].items[0]
    # The public preview carries the same exact artifact identity plus download path.
    preview = sdk.preview_version_sample(**args, sample_id=first.sample_id)
    content = io.BytesIO()
    sdk.download_verified(preview, content)
    assert content.getvalue() in bodies
    initialize(f)
    outcome = tool(f, "version_get", {key: str(value) for key, value in args.items()})
    assert outcome["structuredContent"]["id"] == str(publication.version_id)
    with sessions() as session:
        events = session.scalars(select(AuditRow).where(AuditRow.actor_id == f.actor)).all()
        assert any(event.payload.get("transport") == "http-bearer" for event in events)
    f.identities.revoke_grant(f.actor, ns)
    with pytest.raises(SDKError) as denied:
        sdk.get_version(**args)
    assert denied.value.status == 403
    assert tool(f, "version_get", {key: str(value) for key, value in args.items()})["isError"]


def test_wrong_kind_audience_scope_and_issuer_fail_both_transports(sdk_identity):
    f = sdk_identity
    initialize(f)
    changes = [
        {"azp": "sdk-worker" if f.kind == "human" else "sdk-human"},
        {"aud": "other"},
        {"scope": "other"},
        {"iss": "https://other.test"},
        {"sub": str(uuid4())},
    ]
    for change in changes:
        headers = {
            "Authorization": "Bearer " + f.token(**change),
            "Accept": "application/json, text/event-stream",
        }
        response = f.http.get("/api/v1/namespaces", headers=headers)
        assert response.status_code in {401, 403}
        response = f.http.post(
            "/mcp/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "namespaces_list", "arguments": {}},
            },
        )
        assert response.status_code in {401, 403}
