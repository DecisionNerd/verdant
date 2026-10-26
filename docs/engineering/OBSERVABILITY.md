# Observability and audit

The target uses **Grafana OSS, Mimir, Loki and Tempo**, fed by the **OpenTelemetry Collector**, for dashboards, metrics, logs, traces and Grafana-managed alerting. Applications export OpenTelemetry; the Collector routes each signal to its backend. PostgreSQL audit/events are authoritative durable records; telemetry loss or retention must not erase business history. This is a design contract, not a claim that exporters or backends are installed.

Use built-in Grafana Keycloak OAuth with strict group/role admission. Keep backend APIs private and authenticate machine paths separately. The initial deployment is a shared operator workspace: dashboard permissions do not provide dataset-level query isolation. See the [Grafana deployment and Keycloak contract](../infra/grafana-keycloak.md) for OSS permission boundaries, persistence, alert ownership and acceptance. Start with small supported deployment modes; defer profiling and distributed topologies until needed.

## Correlation

Propagate request/trace context, operation ID, event ID, causation/correlation ID, dataset/version reference and Prefect/MLflow/Label Studio run references across transports, services and workers. Include actor/namespace in authorized audit records; avoid sensitive or high-cardinality identity in metric labels.

Trace HTTP/MCP admission → authorization → application transaction → outbox delivery → Prefect/DataChain execution → artifact write → result registration. Link asynchronous attempts to their originating operation. SSE progress is an observational projection of durable operation state.

## Signals

| Area | Signals / investigation |
|---|---|
| API/MCP | Latency/error rates, authorization denials, request saturation; correlate failures to operation |
| Delivery | Outbox age/backlog, inbox duplicates, retries, expired claims, quarantined events |
| Workflows | Queue age, duration, throughput, stalled runs, cancellation latency, external reconciliation |
| Publication | Gate failures, explicit current-draft conflicts, effect-receipt reuse and retention readiness, manifest verification and finalization failures |
| Storage | Missing/corrupt objects, upload failures, digest mismatch, staged orphan age |
| Quality | Versioned policy results, Evidently drift/quality changes, annotation rejection/review age |
| Recovery | Backup age, restore drill outcomes, retained-manifest resolution |

Choose alert thresholds from measured workloads and agreed SLOs. Alerts identify owner, affected operation/version and a recovery action. A failing quality report is a domain outcome, not automatically a system incident; alert by policy/severity.

## Audit and privacy

Record actor, namespace, action, target, outcome, time and relevant revision changes for publication, access-policy changes, privileged reads, callback imports, replay and cancellation. Restrict audit access and define retention. Never emit secrets, tokens, signed URLs, raw private samples or annotation payloads into ordinary logs. Redact provider errors before returning them to clients.

## Incident path

Find the durable operation → inspect correlated trace and external run → distinguish unknown dispatch from execution failure → reconcile before retry → verify resulting manifest and audit. Telemetry downtime must not cause state changes to appear successful or justify bypassing validation.

Export telemetry asynchronously with bounded buffers; shed telemetry with visible loss counters rather than blocking domain commits or growing memory without limit. Audit and outbox remain transactional platform state. Rebuildable projections report checkpoint/freshness and recovery progress; a projector outage degrades projected reads, not authoritative publication/access checks.

## Durable backing and duplication controls

Grafana uses a dedicated database/user in the existing PostgreSQL service. Mimir stores long-term Prometheus-compatible metrics in S3-compatible object storage; Loki and Tempo use separate private buckets on that same store. The Collector writes metrics directly to Mimir, so the default profile has no standalone Prometheus server. Bounded WAL/queues/cache remain where required by the selected backend modes. Follow [Storage and service ownership](../infra/storage-ownership.md) for the authority map, alert ownership and no-duplicate-ingestion proof.
