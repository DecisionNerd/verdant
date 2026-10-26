# Deploy on AWS

**Target recipe, not a verified Verdant deployment.** Start with ECS/Fargate for the Python API and persistent workers, RDS PostgreSQL for authoritative state, S3 for artifacts and a static CDN frontend. Follow [shared prerequisites and acceptance](README.md); Kubernetes is not required.

## Procedure

1. Select account/region and billing owner. Provision network/subnets, service routing and narrowly scoped security groups. API/worker tasks must reach PostgreSQL, object storage, Prefect and chosen model providers; private subnets require a deliberate outbound path, not an assumption of internet access.
2. Create an ECR repository and publish immutable API/worker image digests. Provision RDS PostgreSQL with workload-appropriate availability and backups. Create private S3 artifact buckets and recovery/retention settings. Do not let lifecycle expiration delete published referenced objects.
3. Define ECS task definitions with the real API/worker commands, resource limits, secret references and task roles. Separate the execution role (image pull/logging/secret injection) from application task permissions (for example bucket access). Fargate scratch storage is disposable, not registry persistence.
4. Run Alembic once as a controlled ECS one-off task with database connectivity. Verify its exit result; only then roll compatible API/worker services. Keep migrations out of replica startup and readiness checks.
5. Deploy an API ECS service behind an HTTPS load balancer with health checks on the actual API port/path. Route FastMCP alongside FastAPI or as a separate task if needed. Configure proxy/SSE idle behavior and OIDC callback URLs; do not cache authenticated API responses.
6. Deploy workers as a separate ECS service with bounded desired count and task/database budgets; no public load balancer is necessary for pollers. Register Prefect execution and use durable claim/effect/checkpoint semantics across task replacements. Add autoscaling only with a suitable backlog signal and database/provider headroom.
7. Publish the Vite output to a private frontend S3 bucket behind CloudFront with suitable origin access and SPA fallback. Keep frontend and artifact buckets separate in policy/lifecycle. Scope SPA fallback so missing API endpoints or asset failures do not become misleading HTML success responses. Supply only public API/origin settings to the build.
8. Run shared acceptance and record task definitions/image digests, service deployments, load-balancer routes, RDS recovery proof, artifact integrity and cost observations. Introduce Label Studio/MLflow/Grafana OSS hosting only as required, with persistence/backups for their own state.

## Recovery and cost

Redeploy a prior compatible task definition and frontend release; preserve schema compatibility, outbox/inbox, effect receipts and published artifacts. Exercise RDS restore and referenced S3 reads together. Separate worker/API pool budgets even when both use the same image and domain code. Evaluate baseline RDS/load-balancer/worker/network costs, object requests/transfer and model charges; a container-only cost estimate is incomplete.

Pulumi's AWS provider can own the registry, network, RDS, S3, ECS and routing resources; verify version coverage and preview replacements. Avoid simultaneous manual service updates and Pulumi ownership of the same desired task definition or capacity.

## Sources

- [ECS services and replacement behavior](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ecs_services.html)
- [Create a Fargate task/service](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ECS_AWSCLI_Fargate.html)
- [ECS task IAM roles](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-iam-roles.html)
- [RDS PostgreSQL](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_PostgreSQL.html)
- [CloudFront private S3 origin access](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html)
