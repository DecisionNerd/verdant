"""Actual FastMCP serialization must preserve the canonical flow digest contract."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from fastmcp import Client, FastMCP

from verdant.model_profiles import config_digest
from verdant.process_flow import Flow, FlowDefinition, PinnedFlowStep
from verdant.process_mcp import install_process_tools


def test_mcp_flow_and_sdk_shared_model_preserve_canonical_alias_and_digest():
    namespace, dataset, flow_id = uuid4(), uuid4(), uuid4()
    definition = FlowDefinition(
        schema="verdant.process.v1", steps=[PinnedFlowStep(name="Trim", kind="preprocess")]
    )
    canonical = definition.model_dump(mode="json", by_alias=True)
    result = Flow(
        id=flow_id,
        dataset_id=dataset,
        name="Flow",
        revision=1,
        definition=definition,
        config_digest=config_digest(canonical),
        created_at=datetime.now(UTC),
    )
    mcp = FastMCP("isolated-serialization-test")

    async def invoke(method, *args, **kwargs):
        return result

    install_process_tools(mcp, None, invoke)

    async def check():
        async with Client(mcp) as client:
            response = await client.call_tool(
                "process_flow_get",
                {
                    "namespace_id": str(namespace),
                    "dataset_id": str(dataset),
                    "flow_id": str(flow_id),
                },
            )
            wire = response.structured_content
            assert wire["definition"] == canonical
            assert config_digest(wire["definition"]) == wire["config_digest"]
            # SDK uses this same public model after HTTP decoding.
            decoded = Flow.model_validate(wire)
            assert decoded.definition.model_dump(mode="json") == canonical

    asyncio.run(check())
