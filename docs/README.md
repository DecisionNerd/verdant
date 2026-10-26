# Verdant documentation

This tree defines Verdant's accepted **target** as a dataset-first production ML platform. It intentionally replaces the earlier image/PDF digestion architecture without constraining the new design to existing engineering. Runtime readiness must be proved separately.

| Read | Purpose |
|---|---|
| [Product](PRODUCT.md) | Audience, value, scope, success measures |
| [Requirements](REQUIREMENTS.md) | Observable acceptance conditions |
| [Design](DESIGN.md) | Human and agent experience contract |
| [Architecture](engineering/ARCHITECTURE.md) | Boundaries, entities, flows, invariants |
| [Supplied baseline](engineering/REFERENCE-BASELINE.md) | Exact source, including its truncation |
| [Implementation](engineering/IMPLEMENTATION.md) | First slice, later slices, decisions to research |
| [Testing](engineering/TESTING.md) | Proof required before capability claims |
| [Infrastructure](infra/README.md) | Pulumi and provider deployment guides |
| [Publishing](engineering/PUBLISHING.md) | Deployment, promotion, recovery |
| [Observability](engineering/OBSERVABILITY.md) | Signals and operational response |
| [Experience](experience/README.md) | Dataset lifecycle journeys |
| [Strategy](strategy/README.md) | Delivery priorities |
| [ADRs](engineering/adrs/README.md) | Current decisions in force |

Starlight content under `apps/docs/src/content/docs/` explains the same target to operators and agents. Keep it aligned with this tree; do not duplicate detailed domain definitions. Starlight is a documentation generator, not the product frontend: the product uses React/Vite.

## Evidence and authority

The user selected the supplied reference architecture. Its sections 1–6 and partial section 7 are preserved as source. Details beyond that source are explicitly derived design guidance or open implementation decisions. The architecture defines the current contract and ADRs record its decisions; tests and deployment receipts establish actual availability.

No runtime migration, dependency installation, or deployment is implied by a documentation edit. Sample fixtures under `data/datasets/` remain test data, not platform architecture documentation.
