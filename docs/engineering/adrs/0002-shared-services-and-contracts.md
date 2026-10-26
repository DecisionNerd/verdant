# ADR-0002: Shared domain authority and flexible execution

- **Status:** Accepted (target architecture)
- **Date:** 2026-10-25
- **Decider:** Repository owner

## Context

Human, agent and background workflows need identical invariants without unnecessary transport latency or operational dependencies.

## Decision

FastAPI and FastMCP adapt shared authorized application services. Managed Python workers call those services directly when practical; remote or differently trusted workers use APIs. Direct execution may use application/persistence modules with appropriate database credentials; it cannot bypass domain rules or immutable-state guards. Domain modules own their tables and expose use cases; the initiating use case owns a shared SQLAlchemy unit of work. External calls occur outside database transactions. Domain rules depend on neither transports nor ORM/provider implementations. Use versioned Pydantic domain, integration and storage contracts, SQL constraints and typed OpenAPI clients/helpers.

## Consequences and verification

Authorize commands/batches once at the application boundary and propagate principal/context through nested calls. Verify direct/API parity and dependency boundaries. Do not require custom credential frameworks, per-record identity-provider calls or separate network services without a concrete need.

See [Architecture](../ARCHITECTURE.md), [Implementation](../IMPLEMENTATION.md) and [Testing](../TESTING.md) for the complete contract and proof plan.
