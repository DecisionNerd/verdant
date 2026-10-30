# Engineering

Verdant's target is a modular Python dataset platform. Existing runtime code is outside this documentation refactor's behavior-preservation boundary.

- [Verified ingestion transport](INGESTION.md): implemented backend commands, limits and remaining execution/UI gates.
- [Identity qualification](IDENTITY-QUALIFICATION.md): implementation, measured proof and remaining gates.
- [Python foundation](PLATFORM-FOUNDATION.md): current backend setup, contracts and proof limits.
- [Architecture](ARCHITECTURE.md): ownership, canonical entities, flows and invariants.
- [Supplied reference](REFERENCE-BASELINE.md): preserved source and truncation notice.
- [Implementation plan](IMPLEMENTATION.md): tracer bullet, later slices, research questions.
- [Testing](TESTING.md): acceptance evidence required for capability claims.
- [Publishing](PUBLISHING.md): target deployment, rollout and recovery.
- [Observability](OBSERVABILITY.md): signals, audit and incident investigation.
- [ADRs](adrs/README.md): current decisions in force.

Quality bar: production data integrity, authorization and recovery. No A/B/C quality-regime mapping is defined in this repository; do not invent one. Apply the explicit requirements and proof in these documents.
