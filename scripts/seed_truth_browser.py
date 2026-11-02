"""Own-data seeding for real HTTPS native SSO/browser truth qualification.

This is test setup, not an authentication bypass. It provisions exact issuer/subject
identities and native IDs through the private operators; browser acceptance must
still complete real independent authorization-code logins in both applications.
"""

import hashlib
import json
import os
import ssl
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from verdant.annotation_contracts import CategoricalSchema
from verdant.annotations import AnnotationService
from verdant.application import RegistryService
from verdant.contracts import DatasetCreate, MembershipWrite
from verdant.identity import IdentityService
from verdant.ingestion import IngestionService, UploadDeclaration, UploadRequest
from verdant.ingestion_runner import IngestionRunner
from verdant.label_studio import LabelStudioSettings
from verdant.persistence import IngestionItemRow, NamespaceRow, make_sessions
from verdant.storage import S3Storage

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "apps/workspace/e2e/truth/.local"
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    str(DIRECTORY / "compose.env"),
    "-p",
    "verdant-truth-bc0aaa6b",
    "-f",
    str(DIRECTORY.parent / "compose.yaml"),
]
SUBJECTS = ["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"]
TEXTS = [
    "The careful delivery was excellent.\n",
    "The response was frustrating and unhelpful.\n",
    "The staff made this a wonderful visit.\n",
    "The product arrived broken and late.\n",
    "An operational review sample awaits human judgment.\n",
]


def private_write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)


def native_shell(code):
    result = subprocess.run(
        [
            *COMPOSE,
            "exec",
            "-T",
            "native",
            "python",
            "/label-studio/label_studio/manage.py",
            "shell",
        ],
        input=code,
        text=True,
        capture_output=True,
        check=True,
        timeout=45,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def main():
    os.umask(0o077)
    environment = json.loads((DIRECTORY / "environment.json").read_text())
    os.environ.update(environment)
    target = make_url(environment["VERDANT_DATABASE_URL"])
    if (
        target.host != "127.0.0.1"
        or target.port != 19232
        or target.database != "verdant_truth_test"
    ):
        raise RuntimeError("Truth fixture can seed only its isolated owned PostgreSQL database")
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DIRECTORY / "fixture.json"
    if output.exists():
        print("Existing truth fixture receipt retained; no seed changes made.")
        return
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    sessions = make_sessions(environment["VERDANT_DATABASE_URL"])
    progress_file = DIRECTORY / "seed-progress.json"
    progress = json.loads(progress_file.read_text()) if progress_file.exists() else {}
    identities = IdentityService(sessions)
    issuer = environment["VERDANT_OIDC_ISSUER"]
    if not progress.get("namespace_id"):
        ns = uuid4()
        with sessions.begin() as session:
            session.add(NamespaceRow(id=ns, name="Automated truth qualification"))
        progress["namespace_id"] = str(ns)
        private_write(progress_file, progress)
    ns = UUID(progress["namespace_id"])
    actors = []
    for subject in SUBJECTS:
        principal = identities.provision(issuer, subject, kind="human")
        identities.grant(principal, ns, "write")
        actors.append(identities.resolve(issuer, subject))
    actor = actors[0]
    registry = RegistryService(sessions)
    if not progress.get("dataset_id"):
        dataset = registry.create_dataset(
            actor,
            ns,
            DatasetCreate(
                name="Automated truth qualification",
                slug="automated-truth",
                modality="text",
            ),
        )
        progress["dataset_id"] = str(dataset.id)
        private_write(progress_file, progress)
    dataset_id = UUID(progress["dataset_id"])
    storage = S3Storage.from_env()
    ingestion = IngestionService(sessions, bucket=storage.settings.bucket, storage=storage)
    runner = IngestionRunner(sessions, storage)
    members = progress.setdefault("members", [])
    for index, text in enumerate(TEXTS):
        if len(members) > index:
            continue
        body = text.encode()
        batch = ingestion.admit(
            actor,
            ns,
            UploadRequest(
                request_key=f"truth-seed-{ns}-{index}",
                items=[
                    UploadDeclaration(
                        source_key=f"truth-{index}",
                        modality="text",
                        sha256=hashlib.sha256(body).hexdigest(),
                        size_bytes=len(body),
                        metadata={
                            "group_id": f"independent-group-{index}",
                            "label": "POISON_ANSWER_METADATA_MUST_NOT_ENTER_MODEL_INPUT",
                            "answer": {"target": "never-send-to-model"},
                        },
                    )
                ],
            ),
        )
        item = batch.items[0]
        if batch.operation.status != "succeeded":
            ingestion.initiate_upload(actor, ns, batch.operation.id, item.id, 1)
            with sessions() as session:
                stored = session.get(IngestionItemRow, item.id)
                if stored.upload_id:
                    storage.client.upload_part(
                        Bucket=storage.settings.bucket,
                        Key=stored.staging_key,
                        UploadId=stored.upload_id,
                        PartNumber=1,
                        Body=body,
                    )
            ingestion.complete_upload(actor, ns, batch.operation.id, item.id, 1)
            for _ in range(10):
                batch = runner.run(actor, ns, batch.operation.id, 1)
                if batch.operation.status == "succeeded":
                    break
            else:
                raise RuntimeError("Truth fixture ingestion did not finish within 10 bounded steps")
        item = batch.items[0]
        current = registry.list_memberships(actor, ns, dataset_id)
        if not any(member.sample_id == item.sample_id for member in current.members):
            registry.set_membership(
                actor,
                ns,
                dataset_id,
                item.sample_id,
                MembershipWrite(
                    expected_revision=current.revision,
                    sample_revision_id=item.accepted_revision_id,
                    split="production" if index == 4 else "train",
                ),
            )
        members.append(
            {
                "index": index,
                "sample_id": str(item.sample_id),
                "sample_revision_id": str(item.accepted_revision_id),
                "sha256": hashlib.sha256(body).hexdigest(),
                "size_bytes": len(body),
                "text": text,
                "partition": "production" if index == 4 else "train",
            }
        )
        private_write(progress_file, progress)
    native = native_shell("""
import json
from django.core.management import call_command
from organizations.models import Organization
from users.models import User
from rest_framework.authtoken.models import Token
from verdant_ls.models import ExternalIdentity, ServiceAccount
from django.conf import settings
if not Organization.objects.exists():
    call_command("bridge_operator", "bootstrap",
        email="recovery@truth-fixture.example.test", reason="isolated-truth-fixture")
org = Organization.objects.get(pk=1)
services = ServiceAccount.objects.filter(user__email="adapter@truth-fixture.example.test")
if not services.exists():
    call_command("bridge_operator", "create-service",
        email="adapter@truth-fixture.example.test", reason="isolated-truth-fixture")
service = ServiceAccount.objects.get(user__email="adapter@truth-fixture.example.test").user
token, _ = Token.objects.get_or_create(user=service)
people = []
for name, subject in [
    ("owner", "00000000-0000-0000-0000-000000000001"),
    ("editor", "00000000-0000-0000-0000-000000000002"),
]:
    identity = ExternalIdentity.objects.filter(issuer=settings.OIDC_ISSUER, subject=subject).first()
    if identity is None:
        user = User.objects.create_user(email=name+"@example.test", password=None, username=name)
        org.add_user(user)
        user.active_organization = org
        user.save(update_fields=["active_organization"])
        call_command("bridge_operator", "link", user_id=user.pk,
            issuer=settings.OIDC_ISSUER, subject=subject,
            reason="isolated-truth-fixture-exact-subject")
        identity = ExternalIdentity.objects.get(issuer=settings.OIDC_ISSUER, subject=subject)
    people.append({"name": name, "native_user_id": identity.user_id,
        "issuer": identity.issuer, "subject": identity.subject})
print(json.dumps({"people": people, "service_user_id": service.pk, "service_token": token.key}))
""")
    environment["VERDANT_LS_TOKEN"] = native.pop("service_token")
    private_write(DIRECTORY / "environment.json", environment)
    schema = CategoricalSchema(
        labels=["positive", "negative"],
        instruction_revision="v1",
        instructions="Choose the sentiment from the text.",
    )
    annotations = AnnotationService(sessions)
    settings = LabelStudioSettings(
        environment["VERDANT_LS_BROWSER_ORIGIN"],
        environment["VERDANT_LS_MACHINE_ORIGIN"],
        environment["VERDANT_LS_IMAGE"],
    )
    cohort_id = annotations.admit_cohort(
        ns,
        name="Isolated real human shared trust",
        settings=settings,
        issuer=issuer,
        reason="Automated qualification isolated native image",
    )
    for person in native["people"]:
        annotations.bind_contributor(
            cohort_id,
            issuer=person["issuer"],
            subject=person["subject"],
            native_user_id=person["native_user_id"],
            reviewer=person["name"] == "owner",
            reason="Private native inventory exact issuer/subject/native ID qualification",
        )
    with httpx.Client(
        base_url=settings.machine_origin,
        headers={
            "Authorization": "Token " + environment["VERDANT_LS_TOKEN"],
        },
        verify=ssl.create_default_context(cafile=environment["SSL_CERT_FILE"]),
        trust_env=False,
        follow_redirects=False,
        timeout=20,
    ) as http:
        if "native_project_id" not in progress:
            response = http.post(
                "/api/projects/",
                json={
                    "title": "Isolated Verdant categorical truth",
                    "label_config": schema.native_xml(),
                    "evaluate_predictions_automatically": False,
                    "show_collab_predictions": False,
                    "maximum_annotations": 2,
                },
            )
            response.raise_for_status()
            progress["native_project_id"] = response.json()["id"]
            private_write(progress_file, progress)
        for member in members:
            if member.get("native_task_id"):
                continue
            response = http.post(
                "/api/tasks/",
                json={
                    "project": progress["native_project_id"],
                    "data": {
                        "text": member["text"],
                        "verdant_revision_id": member["sample_revision_id"],
                    },
                },
            )
            response.raise_for_status()
            member["native_task_id"] = response.json()["id"]
            private_write(progress_file, progress)
        exported = http.get(
            f"/api/projects/{progress['native_project_id']}/export?exportType=JSON&download_all_tasks=true"
        )
        exported.raise_for_status()
        assert len(exported.json()) == 5
    result = {
        "environment": "isolated-owned-real-https-keycloak-native-postgresql-s3",
        "namespace_id": str(ns),
        "dataset_id": str(dataset_id),
        "owner_id": str(actors[0].id),
        "editor_id": str(actors[1].id),
        "cohort_id": str(cohort_id),
        "native_project_id": progress["native_project_id"],
        "native_people": native["people"],
        "service_user_id": native["service_user_id"],
        "schema": schema.model_dump(),
        "members": members,
        "draft_revision": registry.list_memberships(actor, ns, dataset_id).revision,
        "image": settings.image,
        "source_revision": "cc81a17bb2a077664083979dbdbb6e685228e08f",
    }
    private_write(output, result)
    storage.close()
    sessions.kw["bind"].dispose()
    print(
        "Seeded five verified text artifacts and native tasks; "
        "exact-subject contributor admission saved."
    )


if __name__ == "__main__":
    main()
