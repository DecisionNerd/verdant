"""Observe the private checkpoint clone; never target a live database.

Caller creates isolated resources, restores native archives and installs a unique
custom DB marker. This observer requires that marker before inspecting data or
resuming the cloned candidate. It emits safe identities/digests, never credentials.
"""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

from prefect.client.orchestration import get_client
from sqlalchemy import MetaData, Table, select, text

from verdant.flows import service_principal
from verdant.persistence import (
    CandidateRow,
    DraftRow,
    GenerationMemberRow,
    MembershipRow,
    OperationRow,
    SampleRevisionRow,
    WorkflowDispatchRow,
    make_sessions,
)
from verdant.storage import ObjectDigest, S3Storage
from verdant.versioning import CandidatePrepare, VersioningService

CANDIDATE = UUID("63591c4c-fc9b-4b17-b47f-909c2b0dcc1e")
NAMESPACE = UUID("dd4a1c4a-cea4-4da4-86c3-e1c30a664539")
DATASET = UUID("d0657eaa-e6aa-4ca7-b6cc-536ee5cccf72")


def digest_rows(rows):
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upgraded", action="store_true")
    args = parser.parse_args()
    expected = json.loads(Path("/qualification/registry-before.json").read_text())
    sessions = make_sessions(os.environ["VERDANT_DATABASE_URL"])
    storage = None
    try:
        with sessions() as session:
            marker = session.scalar(
                text("SELECT current_setting('verdant.restore_qualification',true)")
            )
            if not marker or marker != os.environ["VERDANT_RESTORE_PROOF_ID"]:
                raise RuntimeError("Observer requires its isolated clone marker")
            schema = session.scalar(text("SELECT version_num FROM alembic_version"))
            assert schema == ("0018_ingestion_sources" if args.upgraded else "0014_interpretation")
            for name, facts in expected["tables"].items():
                # Reflect actual restored tables; SQL identifiers are native quoted metadata.
                table = Table(name, MetaData(), autoload_with=session.connection())
                rendered = session.execute(select(table)).mappings().all()
                assert len(rendered) == facts["count"]
                # Match the native source SQL row hash, including its JSON text representation.
                quoted = session.bind.dialect.identifier_preparer.quote(name)
                count, digest = session.execute(
                    text(
                        f"SELECT count(*),md5(COALESCE(string_agg(row_to_json(t)::text,E'\\n' "
                        f"ORDER BY row_to_json(t)::text),'')) FROM public.{quoted} t"
                    )
                ).one()
                assert count == facts["count"] and digest == facts["md5"], name
            candidate = session.get(CandidateRow, CANDIDATE)
            draft = session.get(DraftRow, candidate.draft_id)
            assert (
                candidate.state,
                candidate.prepared_count,
                candidate.source_revision,
                draft.revision,
            ) == ("preparing", 100, 102, 102)
            expected_members = (
                session.execute(
                    select(
                        MembershipRow.sample_id,
                        MembershipRow.sample_revision_id,
                        MembershipRow.split,
                        MembershipRow.interpretation,
                    )
                    .where(
                        MembershipRow.draft_id == candidate.draft_id,
                        MembershipRow.valid_from <= 102,
                        (MembershipRow.valid_to.is_(None)) | (MembershipRow.valid_to > 102),
                    )
                    .order_by(MembershipRow.sample_id)
                )
                .mappings()
                .all()
            )
            expected_members = [dict(row) for row in expected_members]
            assert len(expected_members) == 101
            dispatches = session.scalars(
                select(WorkflowDispatchRow).where(WorkflowDispatchRow.status == "submitted")
            ).all()
            verified = session.scalars(
                select(OperationRow).where(
                    OperationRow.kind == "verify_sample_artifact",
                    OperationRow.status == "succeeded",
                )
            ).all()
            objects = []
            for operation in verified:
                revision = session.get(SampleRevisionRow, UUID(operation.inputs["revision_id"]))
                assert revision.namespace_id == operation.namespace_id
                objects.append((revision.object_key, revision.sha256, revision.size_bytes))
        # Actual service credential/JWKS verification against restored Keycloak and registry.
        actor = service_principal(sessions)
        assert NAMESPACE in actor.writable_namespaces
        storage = S3Storage.from_env()
        for key, sha256, size in objects:
            storage.verify(key, ObjectDigest(sha256, size))
        assert objects and dispatches

        async def providers():
            result = []
            async with get_client() as client:
                for dispatch in dispatches:
                    run = await client.read_flow_run(UUID(dispatch.provider_reference))
                    assert run.idempotency_key == dispatch.invocation_key
                    assert run.deployment_id == dispatch.deployment_id
                    assert run.parameters == {
                        "namespace_id": str(dispatch.namespace_id),
                        "operation_id": str(dispatch.operation_id),
                        "epoch": dispatch.epoch,
                    }
                    result.append({"provider_run_id": str(run.id), "state": run.state.name})
            return result

        result = {
            "schema": schema,
            "all_old_table_hashes_match": True,
            "old_table_count": len(expected["tables"]),
            "partial_count": 100,
            "expected_frozen_member_count": 101,
            "source_members_sha256": digest_rows(expected_members),
            "restored_keycloak_service_identity_verified": True,
            "verified_original_object_count": len(objects),
            "provider_references": asyncio.run(providers()),
        }
        if args.upgraded:
            prepared = VersioningService(sessions).prepare(
                actor, NAMESPACE, DATASET, CANDIDATE, CandidatePrepare(expected_count=100)
            )
            assert prepared.state == "sealed" and prepared.prepared_count == 101
            with sessions() as session:
                actual = (
                    session.execute(
                        select(
                            GenerationMemberRow.sample_id,
                            GenerationMemberRow.sample_revision_id,
                            GenerationMemberRow.split,
                            GenerationMemberRow.interpretation,
                        )
                        .where(GenerationMemberRow.generation_id == prepared.generation_id)
                        .order_by(GenerationMemberRow.sample_id)
                    )
                    .mappings()
                    .all()
                )
            assert digest_rows([dict(row) for row in actual]) == result["source_members_sha256"]
            result["cloned_candidate_resumed_and_sealed_with_original_pins"] = True
        print(json.dumps(result))
    finally:
        if storage is not None:
            storage.close()
        sessions.kw["bind"].dispose()


if __name__ == "__main__":
    main()
