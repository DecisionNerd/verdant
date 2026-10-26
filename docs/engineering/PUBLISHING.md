# Delivery and operations

Target deployment uses a static React/Vite frontend and containerized Python control plane, workers and Prefect flows, backed by PostgreSQL and S3-compatible storage. Label Studio, MLflow, Evidently and Grafana OSS are integration/deployment concerns with separately managed credentials and lifecycle. Their exact hosting/version topology is unresolved.

**Readiness:** existing Compose files, Dockerfiles and commands may still deploy the earlier digestion stack. They are not an installation recipe for this architecture. This docs change performs no runtime deployment or data migration.

Provider procedures and Pulumi ownership are documented in [Infrastructure](../infra/README.md). Follow one provider guide plus the common deployment acceptance contract.

## Implementation release gates

1. Record the exact source, contract/schema versions and migration compatibility window.
2. Validate authorization, idempotency, publication immutability, outbox/inbox recovery and artifact integrity using [Testing](TESTING.md).
3. Apply Alembic changes with expand/contract discipline; deploy compatible readers/consumers before new event/manifest producers.
4. Run an isolated end-to-end acceptance flow, including unauthorized denial, duplicate processing and recovery. Observe actual provider integrations before claiming them ready.
5. Promote through repository-specific release policy, then run the same live smoke scenario and inspect telemetry/audit. Define numeric SLOs and resource budgets before production release.

No universal staging/main policy is introduced here. Do not infer production authorization or paid-resource approval from this plan.

## Rollback and recovery

Prefer rollback of application code while retaining additive schema changes and compatible readers. Never rewrite published manifests or remove their referenced revisions to roll back a release. Pause failing consumers safely, inspect durable operations/outbox/inbox, and replay only through audited idempotent paths. Reconcile uncertain external runs before resubmission.

Backups cover PostgreSQL, immutable artifacts/manifests, integration mappings/configuration and recoverable secret references. Restore drills verify every retained snapshot resolves its expected digests. Decide retention, RPO/RTO, encryption and garbage-collection grace periods before enabling deletion. Deprecation/archive policy must distinguish access changes from destruction.

## Documentation delivery

Starlight remains the documentation site. Build locally with `pnpm --filter @verdant/docs build`; publish using the repository's reviewed documentation workflow. `SITE`/`BASE` configure the static docs site. Documentation publication does not deploy the platform.

## Deployment simplicity and continuity

Start with the fewest operational boundaries that fit the workload: one Python application deployment plus managed processing workers, with direct service calls when co-located/trusted. Keep third-party integration access separate; network APIs are available where isolation or language/runtime requires them. Avoid making normal work depend on remote identity introspection, telemetry ingestion or a projection being fresh. Validate degraded-mode behavior and background recovery as part of promotion.

Archiving preserves retained exact-version access and admitted-work checkpoints. Unarchive restores admission/visibility; deletion follows its own retention and authorization policy. See the architecture lifecycle table for transitions.

## Organizational identity release gate

Compose and Kubernetes use self-hosted Keycloak and the [service integration matrix](../infra/local-auth.md). A release claiming full-cluster SSO must demonstrate one frontend login followed by every enabled dashboard as the same individual, plus noninteractive API/service access. Missing SSO licenses/plugins or double-login behavior are explicit unmet prerequisites; do not compensate with shared accounts or label a proxy gate as native identity integration. See the [Kubernetes rollout](../infra/kubernetes.md).
