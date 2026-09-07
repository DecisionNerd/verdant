# Product

Verdant is a **local-first Compose stack** that turns images and PDFs into structured
Markdown (and optional HTML) with Mermaid charts and artifacts. Humans use the Web UI or
CLI; coding agents use MCP. Self-hosted Trigger.dev and Langfuse support durable jobs and
truth-fixture evaluation without sending documents to a third-party SaaS by default.

## Problem

Scanned PDFs and screenshots are hard for agents and note systems to use: OCR and generic
extractors drop structure, invent layout chrome, or flatten diagrams. Teams that want
**local control** (keys, data on disk, self-hosted eval) lack a single bring-up that covers
digest → preview → MCP → eval. Verdant exists so a `docker compose up` is enough to digest
visually rich documents into living Markdown for people and agents.

## Audience

- **Operators / power users** — run Verdant locally, configure an LLM, digest PDFs, inspect
  outputs under `data/outputs`.
- **Coding agents (via MCP)** — call digest/status/read tools and open host-relative paths in
  the workspace.
- **Evaluators / fork maintainers** — sync truth fixtures to Langfuse, compare models, customize
  docs and defaults for a deployment.

## Vision

Local agents and humans treat scanned documents as first-class Markdown sources: page-aware
digests, clean structure (no page chrome), charts as Mermaid, artifacts on disk, and evals
against fixtures — all on a laptop or private server.

## Goals

- One-command local bring-up with durable data under `./data`.
- Faithful multipage PDF digestion (diagnose → plan → page digests → stitch → preview).
- First-class agent access via always-on MCP (plus CLI/stdio fallback).
- Self-hosted observability and eval (Langfuse) without mandatory cloud vendors.
- Fork-friendly docs: Starlight for operators; in-repo DocSlime for product/engineering truth.

## Quality stance

- Prefer **observable behavior** (Given/When/Then in requirements and eval fixtures) over
  speculative architecture.
- Record durable technical choices as **ADRs** under `engineering/adrs/`.
- UI work follows `DESIGN.md` (and tools that load it); end-user how-tos live in Starlight
  (`apps/docs`), not duplicated here.
- Langfuse truth fixtures in `data/datasets` are the product’s primary quality signal for
  digest quality.

## Non-goals

- Hosted multi-tenant SaaS or managed cloud product.
- Perfect OCR of every language/layout without an LLM (vision LLM is required).
- Replacing general document editors (Notion, Word) or full RAG platforms.
- Guaranteeing Trigger.dev dashboard runs for every digest (filesystem worker queue is the
  reliable local path; Trigger is optional orchestration).

## Success Metrics

- Operator can configure an LLM and complete a first digest within one session after compose up.
- Multipage PDFs produce `document.md` plus per-page artifacts without silent blank-page waste.
- Agents can digest and read outputs via MCP using host-relative paths.
- Eval CLI can score a fixture set against structure + similarity when Langfuse (or offline
  sync file) is available.

## Stakeholders

- **Maintainers / DecisionNerd** — product direction and Compose defaults.
- **Fork operators** — local deployments and customized Starlight pages.
- **Downstream agents** — MCP consumers that treat Verdant as a document tool.
