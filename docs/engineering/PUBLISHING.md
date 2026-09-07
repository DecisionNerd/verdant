# Publishing

Verdant ships as a **Compose-first local stack**. What “publish” means today: rebuild
container images for local/fork use, and optionally deploy the Starlight docs site to
GitHub Pages.

## Artifacts and destinations

| Artifact | Destination | Versioned by | Owner |
|---|---|---|---|
| `verdant-web` / `verdant-worker` / `cli` / `docs` images | Local Docker (Compose) | Image build from git SHA / local tag | Maintainers |
| Starlight static site | GitHub Pages | Commit on `main` touching `apps/docs/**` | Maintainers / forks |
| Source repo | GitHub | Git history | Maintainers |

There is no separate public npm release of `@verdant/core` yet.

## Suggested versioning and change history

- Compose images are currently **source-tied** (rebuild from the repo) rather than SemVer’d
  on a registry.
- Consider Conventional Commits and SemVer if/when CLI or core is published as a standalone
  package; do not enforce without explicit agreement.
- Docs Pages deploy is independent of app image tags.

## Build and continuous delivery

**Local stack**

```bash
docker compose up -d --build
```

**Docs (CI)**

- Workflow: [`.github/workflows/deploy-docs.yml`](../../.github/workflows/deploy-docs.yml)
- Sets `SITE=https://<owner>.github.io` and `BASE=/<repo>` for fork-friendly Pages.

**Gates before calling a change “shipped” locally**

1. Relevant package build/typecheck passes.
2. Compose rebuild of touched services succeeds.
3. Smoke: Web 200 on configured port; optional MCP/docs ports.
4. If docs changed: `pnpm --filter @verdant/docs build` (and Pages workflow on `main`).

## Promotion and environments

| Environment | What runs there | How you promote |
|---|---|---|
| Developer laptop | Full Compose stack | `docker compose up -d --build` |
| Fork operator host | Same | Pull fork + compose |
| GitHub Pages | Static Starlight only | Push to `main` under `apps/docs/**` |

No staging→production app promotion path exists beyond “rebuild Compose on the target host.”

## Rollback

- **App:** redeploy previous git revision (`git checkout` / pin commit) and
  `docker compose up -d --build`.
- **Data:** `./data` is not overwritten by image rebuilds; restore from backup if needed.
- **Docs:** GitHub Pages serves the last successful workflow artifact; revert the docs commit
  and re-run the workflow.
- **Langfuse seed user:** only created on empty DB; changing seed credentials may require
  volume reset.

## Ownership

| Area | Owner |
|---|---|
| Compose defaults & images | Maintainers |
| Starlight / Pages | Maintainers (forks customize `apps/docs`) |
| DocSlime product docs | Maintainers (keep in sync with behavior) |
