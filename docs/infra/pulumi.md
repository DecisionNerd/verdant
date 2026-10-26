# Pulumi infrastructure workflow

Local development follows [Docker Compose](docker-compose.md); Pulumi is not required to start the local platform.

Use Pulumi to provision the selected deployment, not to implement dataset operations or schema migrations. This is a documentation contract; no `Pulumi.yaml`, provider program or deployment pipeline is added. Follow the [shared rollout](README.md) after implementing and reviewing the chosen stack.

## Project and ownership

Place the initial program under `infrastructure/pulumi/` when implementing IaC; keep one project and separate environment stacks unless independent lifecycle ownership justifies more. Choose Python or TypeScript to fit maintainers, without introducing a language solely for IaC. Pin CLI/provider versions and provider account/region explicitly.

Choose Pulumi Cloud or a supported self-managed backend with controlled access, locking and recoverable state backups. State may contain sensitive resource properties; keep decryption material recoverable and avoid plaintext state exports. AWS, Google Cloud and Cloudflare have Pulumi registry packages; verify exact resource/version support before choosing a provider. Fly.io/Railway may require a community provider or reviewed native CLI/API path. Provider availability is not a promise of complete platform coverage. Keep unsupported resource ownership documented rather than hiding shell calls inside a sprawling custom provider.

One system owns each resource. Do not let CLI auto-deploy and Pulumi both manage the same app's scaling, routing or database lifecycle. Import existing resources before assuming ownership, reviewing physical names/IDs and replacement behavior. Native CLI commands in the provider guides are alternatives or explicitly delegated steps, not instructions to overwrite Pulumi-managed state.

## Resource plan

Provision only what the slice needs: account/project network boundaries, image registry, PostgreSQL/backups, artifact bucket/retention, API/worker compute, identities/secrets and frontend routing. Integrations may be external services. Export nonsecret endpoints and resource IDs for deployment; mark secret-derived outputs appropriately and avoid public bucket URLs for private data.

Protect retained databases, buckets and critical stateful resources against accidental deletion/replacement; use provider-native recovery controls too. Explicitly review force-delete/empty-bucket settings and retain data on stack teardown. Temporary preview resources must never share production state or receive production credentials. Default to one environment stack, not a multi-cloud abstraction layer.

## Operator sequence

Once a real project exists, run from its directory using the intended backend and provider identity:

```sh
pulumi login
pulumi stack init dev
pulumi config set deploymentRegion YOUR_REGION
pulumi preview --diff
pulumi up
pulumi stack output
```

`deploymentRegion` is a proposed project config key, not an existing Verdant setting; the implemented program must read it and configure its provider. For an existing stack use `pulumi stack select dev` instead of `init`. Choose the backend explicitly with `pulumi login <backend-url>` when not using the default. Supply secrets through a supported secret manager or `pulumi config set --secret` with interactive input, never literal credentials in committed commands. Preview first, review replacements/costs, then apply the exact reviewed source/configuration.

After application and migration artifacts exist: provision → publish image digest → migrate once → roll API/workers → deploy frontend → run [acceptance](README.md). Keep database migrations outside infrastructure replacement logic. Serialize updates to a stack; ensure CI uses the same backend/provider identity scope and protected secret configuration.

## Drift, failure and recovery

Preview/up compare desired resources with recorded state; they do not automatically refresh all provider state. When drift is suspected, review `pulumi refresh` and then preview the resulting changes before updating. A failed update is not an automatic rollback: inspect actual resources/state, resolve partial operations and reapply a reviewed program. Reverting source and applying may itself replace resources; review before doing so.

Never use `pulumi destroy` as application rollback. Recover backend state/decryption access separately from PostgreSQL/artifact backups; restoring IaC state does not restore dataset bytes. Retain effect receipts and published references during replacement or migration of compute.

## Sources

- [Pulumi state and backends](https://www.pulumi.com/docs/iac/concepts/state-and-backends/)
- [Secrets handling](https://www.pulumi.com/docs/iac/concepts/secrets/)
- [Stack management and protection](https://www.pulumi.com/docs/iac/operations/stack-management/)
- [Infrastructure FAQ: refresh and rollback](https://www.pulumi.com/docs/support/faq/infrastructure/)
- [Provider registry](https://www.pulumi.com/registry/)

Object-store outputs should expose nonsecret store IDs/endpoints/region/bucket mappings; credentials stay secret. Use [SeaweedFS locally and qualified cloud storage](local-object-storage.md) without making local hostnames part of dataset identity. Infrastructure replacement cannot stand in for copying/verifying retained objects or coordinating integration-native bucket cutover.
