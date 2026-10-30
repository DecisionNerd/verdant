"""Actual isolated Prefect ingestion failure/retry; never stop shared services.

Uses the existing integrated test DB, fixed local fixture endpoints and a distinct
pool. A failing worker points only its S3 client at a closed loopback port. Stop
that owned worker before retrying with a healthy worker. Logs remain private.
"""

import argparse
import asyncio
import json
import os
import socket
from pathlib import Path
from uuid import UUID

import qualify_ingestion_execution as native
from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import FlowRunFilter, FlowRunFilterIdempotencyKey
from qualify_prefect_execution import configure
from sqlalchemy import func, select

from verdant.flows import service_principal
from verdant.ingestion import IngestionService
from verdant.operations import OperationService
from verdant.persistence import EffectReceiptRow, SampleRevisionRow, SampleRow, make_sessions
from verdant.storage import ObjectDigest, ObjectMissing, S3Storage

RECEIPT = Path("/tmp/verdant-issue14-native-fault.json")
POOL = "verdant-issue14-native-fault"


async def provider(sessions, actor, ns, op, epoch):
    dispatch = OperationService(sessions).workflow_dispatch(actor, ns, op, epoch)
    async with get_client() as client:
        run = await client.read_flow_run(UUID(dispatch.provider_reference))
        matches = await client.read_flow_runs(
            flow_run_filter=FlowRunFilter(
                idempotency_key=FlowRunFilterIdempotencyKey(any_=[dispatch.invocation_key])
            ),
            limit=2,
        )
    assert len(matches) == 1 and run.state.is_final()
    assert run.deployment_id == dispatch.deployment_id
    assert run.idempotency_key == dispatch.invocation_key
    assert run.parameters == {"namespace_id": str(ns), "operation_id": str(op), "epoch": epoch}
    return {
        "operation_id": str(op),
        "epoch": epoch,
        "run_id": str(run.id),
        "state": run.state.name,
        "matching_runs": len(matches),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=["prepare", "worker-failing", "worker", "failed", "retry", "observe"]
    )
    args = parser.parse_args()
    url = configure()
    if not url.endswith("/verdant_issue14_integrated_test"):
        raise RuntimeError("Requires the isolated integrated proof database")
    native.RECEIPT, native.POOL = RECEIPT, POOL
    os.environ["PREFECT_WORKER_WEBSERVER_PORT"] = "18425"
    os.environ["PREFECT_SERVER_ANALYTICS_ENABLED"] = "false"
    if args.mode.startswith("worker"):
        if args.mode == "worker-failing":
            with socket.socket() as probe:
                probe.settimeout(1)
                if probe.connect_ex(("127.0.0.1", 18999)) == 0:
                    raise RuntimeError("Fault endpoint must be a closed loopback port")
            os.environ["VERDANT_S3_ENDPOINT"] = "http://127.0.0.1:18999"
        executable = native.ROOT / ".venv/bin/prefect"
        os.execv(
            executable,
            [
                str(executable),
                "worker",
                "start",
                "--pool",
                POOL,
                "--type",
                "process",
                "--limit",
                "1",
                "--with-healthcheck",
            ],
        )
    sessions, storage = make_sessions(url), S3Storage.from_env()
    try:
        if args.mode == "prepare":
            if RECEIPT.exists():
                raise RuntimeError("Owned receipt exists; inspect before creating new work")
            native.prepare(
                sessions,
                storage,
                verification_name="issue14-fault-regression",
                ingestion_name="issue14-fault-local",
            )
            return
        receipt = json.loads(RECEIPT.read_text())
        actor = service_principal(sessions)
        ns, op = UUID(receipt["namespace_id"]), UUID(receipt["operation_id"])
        assert str(actor.id) == receipt["principal_id"]
        service = IngestionService(sessions, bucket=storage.settings.bucket, storage=storage)
        operations = OperationService(sessions)
        if args.mode == "failed":
            batch = service.get(actor, ns, op)
            assert batch.operation.status == "failed" and batch.items[0].state == "ready"
            assert batch.items[0].error == "storage_unavailable"
            failed = asyncio.run(provider(sessions, actor, ns, op, 1))
            assert failed["state"] == "Failed"
            with sessions() as session:
                for model in (SampleRow, SampleRevisionRow):
                    assert (
                        session.scalar(
                            select(func.count()).select_from(model).where(model.namespace_id == ns)
                        )
                        == 0
                    )
                effect = session.scalar(
                    select(EffectReceiptRow).where(
                        EffectReceiptRow.operation_id == op,
                        EffectReceiptRow.identity["kind"].as_string() == "artifact.promote",
                    )
                )
                assert effect.status == "unknown"
                receipt["promotion_receipt_id"] = str(effect.id)
            receipt["failed_provider"] = failed
            receipt["failed_without_sample_acceptance"] = True
        elif args.mode == "retry":
            assert receipt.get("failed_without_sample_acceptance")
            operations.retry(actor, ns, op, 1)
            asyncio.run(native.tick(sessions, actor, ns, receipt))
            receipt["retried_epoch"] = 2
        else:
            asyncio.run(native.tick(sessions, actor, ns, receipt))
            batch = service.get(actor, ns, op)
            if batch.operation.status != "succeeded" or batch.cleanup_operation_id is None:
                print(json.dumps({"ingestion": batch.operation.status, "cleanup": "pending"}))
                return
            cleanup = operations.get(actor, ns, batch.cleanup_operation_id)
            if cleanup.status != "succeeded":
                print(json.dumps({"ingestion": batch.operation.status, "cleanup": cleanup.status}))
                return
            item = batch.items[0]
            storage.verify(
                f"{ns}/artifacts/{item.artifact_id}", ObjectDigest(item.sha256, item.size_bytes)
            )
            try:
                storage.size(f"{ns}/staging/{item.id}")
            except ObjectMissing:
                pass
            else:
                raise RuntimeError("Owned staging remains after cleanup")
            with sessions() as session:
                for model in (SampleRow, SampleRevisionRow):
                    assert (
                        session.scalar(
                            select(func.count()).select_from(model).where(model.namespace_id == ns)
                        )
                        == 1
                    )
                effects = session.scalars(
                    select(EffectReceiptRow).where(
                        EffectReceiptRow.operation_id == op,
                        EffectReceiptRow.identity["kind"].as_string() == "artifact.promote",
                    )
                ).all()
                assert len(effects) == 1 and effects[0].status == "succeeded"
                assert str(effects[0].id) == receipt["promotion_receipt_id"]
            receipt["recovered_provider"] = asyncio.run(provider(sessions, actor, ns, op, 2))
            receipt["cleanup_provider"] = asyncio.run(
                provider(sessions, actor, ns, batch.cleanup_operation_id, 1)
            )
            assert (
                receipt["recovered_provider"]["state"]
                == receipt["cleanup_provider"]["state"]
                == "Completed"
            )
            receipt.update(
                final_bytes_verified=True,
                owned_staging_removed=True,
                one_sample_and_revision=True,
                stable_promotion_receipt=True,
            )
        RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps({"mode": args.mode, "operation_id": str(op), "verified": True}))
    finally:
        storage.close()
        sessions.kw["bind"].dispose()


if __name__ == "__main__":
    main()
