"""Read exact version identity through the authenticated MCP transport."""

import argparse
import asyncio
import os
from uuid import UUID

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport


async def consume(args):
    transport = StreamableHttpTransport(
        os.environ["VERDANT_API_URL"].rstrip("/") + "/mcp/", auth=os.environ["VERDANT_ACCESS_TOKEN"]
    )
    async with Client(transport) as client:
        capabilities = await client.call_tool("platform_capabilities", {})
        result = await client.call_tool(
            "version_get",
            {
                "namespace_id": str(args.namespace),
                "dataset_id": str(args.dataset),
                "version_id": str(args.version),
            },
        )
        if capabilities.is_error or result.is_error:
            raise RuntimeError("MCP command did not produce a committed outcome")
        print(f"version={result.structured_content['id']} authorized=true")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("namespace", type=UUID)
    parser.add_argument("dataset", type=UUID)
    parser.add_argument("version", type=UUID)
    asyncio.run(consume(parser.parse_args()))


if __name__ == "__main__":
    main()
