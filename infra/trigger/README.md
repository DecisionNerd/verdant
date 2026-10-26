# Legacy Trigger.dev infrastructure

This directory belongs to the earlier document-digestion runtime. It is outside the accepted dataset-platform execution architecture and is not a setup path for the new platform.

The target uses **Prefect** for orchestration, **DataChain** for processing, and PostgreSQL outbox/inbox plus durable operations for integration delivery. See [Architecture](../../docs/engineering/ARCHITECTURE.md) and [ADR-0004](../../docs/engineering/adrs/0004-workflows-and-integrations.md).

This documentation refactor does not remove infrastructure, migrate data, or start services. Do not reuse legacy development login/bootstrap behavior as a production authentication design. Any later retirement must inventory running workloads and retained artifacts, preserve required data, and provide a tested recovery path.
