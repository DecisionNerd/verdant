---
title: MCP for agents
description: Connect Cursor and other local agents to Verdant.
---

## Always-on HTTP (recommended)

After `docker compose up`, MCP is available at:

```text
http://localhost:18702/mcp
```

Point your MCP client at that URL (transport depends on the client; many support Streamable
HTTP / SSE against this path).

### Tools

| Tool | Purpose |
| --- | --- |
| `digest_document` | Start a digest |
| `get_digest_status` | Poll job status / progress |
| `list_outputs` | List run outputs |
| `read_output` | Read markdown/html for a run |
| `sync_dataset` | Sync truth fixtures toward Langfuse |
| `run_eval` | Run an evaluation experiment |

Tool responses include host-relative paths like `data/outputs/<runId>/document.md` so agents
can open files in the workspace.

## Cursor stdio fallback

Use this when HTTP MCP is unavailable. Replace the compose file path with your clone:

```json
{
  "mcpServers": {
    "verdant": {
      "command": "docker",
      "args": [
        "compose",
        "-f",
        "/absolute/path/to/verdant/docker-compose.yml",
        "run",
        "--rm",
        "-T",
        "cli",
        "mcp",
        "--stdio"
      ]
    }
  }
}
```

## Prerequisites

- Compose stack up (worker + MCP service)
- An LLM configured (Web UI Settings or `.env` / `data/config.json`)

Operator UI walkthrough: [Web UI](../web-ui/). Bring-up: [Getting started](../getting-started/).
