"""Native Prefect fixture control. Fixed process deployment, real OIDC service identity."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import WorkPoolCreate
from prefect.workers.process import ProcessWorker

from verdant.dispatch import WorkflowDispatcher
from verdant.flows import process_operations, service_principal
from verdant.persistence import make_sessions

POOL = "verdant-process-e2e"
NAME = "qualification"


async def register(path):
    async with get_client() as client:
        await client.create_work_pool(
            WorkPoolCreate(
                name=POOL,
                type="process",
                base_job_template=ProcessWorker.get_default_base_job_template(),
            ),
            overwrite=True,
        )
    definition = await process_operations.to_deployment(
        name=NAME, work_pool_name=POOL, job_variables={"working_dir": str(Path.cwd())}
    )
    deployment = await definition.apply()
    fixture = json.loads(path.read_text())
    fixture["deployment_id"] = str(deployment)
    path.write_text(json.dumps(fixture, indent=2))


async def dispatch(path):
    fixture = json.loads(path.read_text())
    sessions = make_sessions(os.environ["VERDANT_DATABASE_URL"])
    actor = service_principal(sessions)
    async with get_client() as client:
        dispatcher = WorkflowDispatcher(
            sessions,
            client,
            UUID(fixture["deployment_id"]),
            process_deployment_id=UUID(fixture["deployment_id"]),
        )
        result = await dispatcher.tick(actor, UUID(fixture["namespace_id"]), limit=100)
        print(json.dumps(result.__dict__))
    sessions.kw["bind"].dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["register", "dispatch"])
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    asyncio.run(register(args.fixture) if args.command == "register" else dispatch(args.fixture))


if __name__ == "__main__":
    main()
