"""Fixed local deployment registration and dispatcher startup using native Prefect APIs."""

import argparse
import asyncio

from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import WorkPoolCreate
from prefect.workers.process import ProcessWorker

from verdant.dispatch import serve
from verdant.flows import verify_artifact

POOL = "verdant-local"
DEPLOYMENT = "local-verification"


async def ensure_pool():
    async with get_client() as client:
        # Native upsert retains the fixed process job template across restarts.
        await client.create_work_pool(
            WorkPoolCreate(
                name=POOL,
                type="process",
                base_job_template=ProcessWorker.get_default_base_job_template(),
            ),
            overwrite=True,
        )


async def dispatch():
    async with get_client() as client:
        deployment = await client.read_deployment_by_name(
            f"verdant-artifact-verification/{DEPLOYMENT}"
        )
    await serve(None, deployment.id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["register", "dispatch"])
    command = parser.parse_args().command
    if command == "register":
        asyncio.run(ensure_pool())
        verify_artifact.to_deployment(
            name=DEPLOYMENT, work_pool_name=POOL, job_variables={"working_dir": "/app"}
        ).apply()
    else:
        asyncio.run(dispatch())


if __name__ == "__main__":
    main()
