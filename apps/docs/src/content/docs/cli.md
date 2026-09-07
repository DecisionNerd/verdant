---
title: CLI
description: Human-facing Verdant CLI via Docker Compose.
---

Run commands inside Compose so paths stay on the host `data/` mount:

```bash
docker compose run --rm cli <command>
```

## Commands

```bash
verdant digest <path> [--format markdown|html|both] [--no-wait] [--model <id>]
verdant status <runId>
verdant outputs [runId]
verdant read <runId> [--file markdown|html]
verdant sync-dataset [--dir data/datasets] [--name verdant-truth]
verdant eval [--dir data/datasets] [--name <experiment>]
verdant worker [--once]
verdant mcp [--stdio] [--port 8790]
```

### Examples

```bash
cp your-scan.pdf data/inputs/
docker compose run --rm cli digest data/inputs/your-scan.pdf --format both
docker compose run --rm cli status <runId>
docker compose run --rm cli read <runId> --file markdown
```

Paths should be host-relative under `data/...` so results remain visible on the host.

For agent access, prefer the always-on HTTP MCP endpoint instead of spawning stdio for every
session — see [MCP for agents](../mcp/).
