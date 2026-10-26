# ADR-0000: Documentation and verification contract

- **Status:** Accepted (target architecture)
- **Date:** 2026-10-25
- **Decider:** Repository owner

## Context

Operators, maintainers and agents need one consistent target design and a clear distinction between intent and verified capability.

## Decision

Keep product, requirements, design, architecture, proof plans and decisions in repository docs/. Publish concise operator/agent guidance with Starlight in apps/docs. The product frontend is React/Vite. ARCHITECTURE.md defines current system semantics; ADRs record the decisions in force. Preserve supplied source in REFERENCE-BASELINE.md with its limitations. Derive observable acceptance conditions and record actual runtime evidence separately.

## Consequences and verification

A documentation build proves content integrity, not platform readiness. Link detailed definitions rather than copying them across every page. Keep fixtures as focused tests; qualify real workflows before claiming availability.

See [Architecture](../ARCHITECTURE.md), [Implementation](../IMPLEMENTATION.md) and [Testing](../TESTING.md) for the complete contract and proof plan.
