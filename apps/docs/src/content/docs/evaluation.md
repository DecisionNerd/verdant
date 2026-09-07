---
title: Evaluation
description: Truth fixtures and Langfuse experiments.
---

## Fixture layout

```text
data/datasets/<case-id>/
  input.png          # or input.pdf / input.jpg
  expected.md        # gold markdown
  meta.json          # optional
```

## Sync to Langfuse

```bash
docker compose run --rm cli sync-dataset --name verdant-truth
```

If Langfuse keys are missing, Verdant writes `data/datasets/_langfuse-sync.json` instead.

## Run eval

```bash
docker compose run --rm cli eval --name gemini-baseline
```

Scores (v1):

- **structure** — headings / Mermaid validity
- **similarity** — token F1 vs expected markdown

Inspect traces and datasets in the local Langfuse UI at http://localhost:18703
(`verdant@example.com` / `verdant1` on first empty boot).

Fixture layout and scoring are the operator-facing quality loop; deeper testing notes for
maintainers live in the repo at `docs/engineering/TESTING.md`.
