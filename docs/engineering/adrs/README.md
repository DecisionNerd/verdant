# Architecture decisions

This is the starting decision set for the dataset platform. The index identifies the current target decisions. [Architecture](../ARCHITECTURE.md) defines the complete current system contract.

| ADR | Decision | Status |
|---|---|---|
| [0000](0000-documentation-contract.md) | Documentation and verification contract | Accepted |
| [0001](0001-dataset-first-platform.md) | Dataset-first platform baseline | Accepted |
| [0002](0002-shared-services-and-contracts.md) | Shared domain authority and flexible execution | Accepted |
| [0003](0003-state-artifacts-and-publication.md) | Immutable snapshots and bounded publication | Accepted |
| [0004](0004-workflows-and-integrations.md) | Durable operations and retry-safe integrations | Accepted |
| [0005](0005-identity-and-observability.md) | Practical identity, durable audit and asynchronous telemetry | Accepted |
| [0006](0006-availability-and-resource-budgets.md) | Recoverable reads, lifecycle and shared-resource budgets | Accepted |
| [0007](0007-local-compose-and-oidc.md) | Organizational Keycloak SSO on Compose and Kubernetes | Accepted |
| [0008](0008-local-s3-and-cloud-portability.md) | Local S3 storage and cloud portability | Accepted |

Accepted means chosen target design, not implemented capability. [Implementation](../IMPLEMENTATION.md) and [Testing](../TESTING.md) define delivery and verification. Add a record with `docslime add adr <slug>` when a new decision is needed and keep this index and the architecture consistent.
