"""Real local Prefect admission proof; no flow execution or cloud calls are claimed."""

import asyncio
import json
import os
from uuid import uuid4

from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import FlowRunFilter, FlowRunFilterIdempotencyKey
from sqlalchemy import select
from sqlalchemy.engine import make_url

from verdant.identity import IdentityService
from verdant.operations import OperationRequest, OperationService
from verdant.persistence import NamespaceRow, OperationEventRow, make_sessions
from verdant.prefect_submission import PrefectSubmission, WorkflowProviderUnavailable


async def main():
    url = os.environ["VERDANT_TEST_DATABASE_URL"]
    if not (make_url(url).database or "").endswith("_test"):
        raise RuntimeError("Use an isolated *_test database")
    if os.environ.get("PREFECT_API_URL") != "http://127.0.0.1:18420/api":
        raise RuntimeError("This qualifier targets only its fixed loopback Prefect server")
    sessions = make_sessions(url)
    identities = IdentityService(sessions)
    subject, ns = str(uuid4()), uuid4()
    identity = identities.provision("https://local-qualification.invalid", subject, kind="service")
    with sessions.begin() as session:
        session.add(NamespaceRow(id=ns, name="Prefect submission qualification"))
    identities.grant(identity, ns, "write")
    actor = identities.resolve("https://local-qualification.invalid", subject, kind="service")
    operations = OperationService(sessions)
    op = operations.admit(
        actor, ns, OperationRequest(request_key=str(uuid4()), kind="qualification")
    )
    with sessions() as session:
        event_id = session.scalar(
            select(OperationEventRow.id).where(OperationEventRow.operation_id == op.id)
        )
    dispatch = operations.receive_workflow_event(actor, ns, event_id).dispatch
    async with get_client() as client:
        flow_id = await client.create_flow_from_name(
            "verdant-admission-qualification-" + uuid4().hex
        )
        deployment = await client.create_deployment(
            flow_id, name="admission-only", enforce_parameter_schema=False
        )

        class LostResponse:
            async def create_flow_run_from_deployment(self, *args, **kwargs):
                await client.create_flow_run_from_deployment(*args, **kwargs)
                raise TimeoutError("Simulated lost response after real provider acceptance")

        try:
            await PrefectSubmission(sessions, LostResponse(), deployment).submit(
                actor, ns, op.id, 1
            )
        except WorkflowProviderUnavailable:
            assert operations.workflow_dispatch(actor, ns, op.id, 1).status == "unknown"
        else:
            raise RuntimeError("Lost response was not surfaced")
        adapter = PrefectSubmission(sessions, client, deployment)
        recovered = await adapter.submit(actor, ns, op.id, 1)
        assert await adapter.submit(actor, ns, op.id, 1) == recovered
        runs = await client.read_flow_runs(
            flow_run_filter=FlowRunFilter(
                idempotency_key=FlowRunFilterIdempotencyKey(any_=[dispatch.invocation_key])
            ),
            limit=2,
        )
        assert len(runs) == 1
        assert recovered.provider_reference == str(runs[0].id)
        print(
            json.dumps(
                {
                    "operation_id": str(op.id),
                    "deployment_id": str(deployment),
                    "provider_run_id": recovered.provider_reference,
                    "provider_runs": len(runs),
                    "dispatch": recovered.status,
                    "response_loss": "reconciled by exact key",
                    "execution": "not part of this admission proof",
                }
            )
        )
    sessions.kw["bind"].dispose()


if __name__ == "__main__":
    asyncio.run(main())
