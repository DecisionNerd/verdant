# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates python3 make g++ \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*
RUN corepack enable && corepack prepare pnpm@10.26.2 --activate
WORKDIR /app

FROM base AS deps
COPY package.json pnpm-workspace.yaml ./
COPY packages/core/package.json packages/core/
COPY apps/cli/package.json apps/cli/
COPY apps/web/package.json apps/web/
COPY apps/docs/package.json apps/docs/
COPY trigger/package.json trigger/
# Prefer lockfile when present
COPY pnpm-lock.yaml* ./
RUN pnpm install --no-frozen-lockfile

FROM deps AS build
COPY tsconfig.base.json ./
COPY packages/core packages/core
COPY apps/cli apps/cli
COPY apps/web apps/web
COPY apps/docs apps/docs
COPY trigger trigger
COPY trigger.config.ts ./
COPY assets assets
# Re-link workspace packages after COPY (source trees replace package dirs)
RUN pnpm install --no-frozen-lockfile \
 && pnpm --filter @verdant/core build \
 && pnpm --filter @verdant/cli build \
 && pnpm --filter @verdant/web build

FROM build AS docs-build
RUN pnpm --filter @verdant/docs build

# ── worker / cli / mcp ───────────────────────────────────────────────
FROM base AS cli
ENV NODE_ENV=production DATA_DIR=/data
COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/package.json /app/pnpm-workspace.yaml ./
COPY --from=build /app/packages/core ./packages/core
COPY --from=build /app/apps/cli ./apps/cli
# Ensure workspace package node_modules links resolve
COPY --from=deps /app/packages/core/node_modules ./packages/core/node_modules
COPY --from=deps /app/apps/cli/node_modules ./apps/cli/node_modules
WORKDIR /app
VOLUME ["/data"]
EXPOSE 8790 8791
ENTRYPOINT ["node", "apps/cli/dist/index.js"]
CMD ["worker"]

# ── web ──────────────────────────────────────────────────────────────
FROM base AS web
ENV NODE_ENV=production DATA_DIR=/data PORT=8787
COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/package.json /app/pnpm-workspace.yaml ./
COPY --from=build /app/apps/web ./apps/web
COPY --from=deps /app/apps/web/node_modules ./apps/web/node_modules
WORKDIR /app/apps/web
EXPOSE 8787
VOLUME ["/data"]
CMD ["pnpm", "exec", "next", "start", "-p", "8787", "-H", "0.0.0.0"]

# ── docs ─────────────────────────────────────────────────────────────
FROM base AS docs
ENV NODE_ENV=production HOST=0.0.0.0 PORT=4321
COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/package.json /app/pnpm-workspace.yaml ./
COPY --from=docs-build /app/apps/docs ./apps/docs
COPY --from=deps /app/apps/docs/node_modules ./apps/docs/node_modules
WORKDIR /app/apps/docs
EXPOSE 4321
CMD ["pnpm", "exec", "astro", "preview", "--host", "0.0.0.0", "--port", "4321"]
