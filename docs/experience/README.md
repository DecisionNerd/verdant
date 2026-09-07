# Experience

Experience artifacts for Verdant cover **operators**, **CLI users**, and **MCP agents**.
There is no separate interview research corpus in-repo yet; principles below are derived from
how the product is actually used.

## Discovery practice

- Learn from local dogfooding, fork feedback, and eval fixture failures.
- Promote a finding to [`../REQUIREMENTS.md`](../REQUIREMENTS.md) when it is stable and
  testable; otherwise keep it as an open question.
- Coding agents are first-class users — MCP path clarity is part of UX.

## Experience principles

- **Time-to-first-digest** — compose up → configure LLM → digest should fit one sitting.
- **Paths over abstractions** — show `data/outputs/<runId>/…` so humans and agents can open files.
- **Progress is visible** — multipage runs expose plan, page dots, and pipeline events.
- **Settings stay secondary** — drawer + alerts; never block the workspace layout permanently.

## Artifact template

When adding a journey or opportunity file, include: problem, evidence, desired outcome,
hypothesis, and links to requirement IDs. Do not invent quotes or users.

## Index

| Document | Description |
|---|---|
| [first-digest-journey.md](first-digest-journey.md) | Operator path from compose up to Markdown preview |
