# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    python3 \
    make \
    g++ \
    && rm -rf /var/lib/apt/lists/*
RUN corepack enable && corepack prepare pnpm@10.26.2 --activate
WORKDIR /app

FROM base AS deps
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml* ./
COPY packages/core/package.json packages/core/
COPY apps/cli/package.json apps/cli/
COPY apps/web/package.json apps/web/
COPY apps/docs/package.json apps/docs/
COPY trigger/package.json trigger/
RUN pnpm install --no-frozen-lockfile

FROM deps AS build
COPY . .
RUN pnpm --filter @verdant/core build \
 && pnpm --filter @verdant/cli build \
 && pnpm --filter @verdant/web build \
 && pnpm --filter @verdant/docs build \
 && pnpm --filter @verdant/trigger build || true

FROM base AS runtime
ENV NODE_ENV=production
ENV DATA_DIR=/data
COPY --from=deps /app/node_modules /app/node_modules
COPY --from=deps /app/packages /app/packages
COPY --from=deps /app/apps /app/apps
COPY --from=deps /app/trigger /app/trigger
COPY --from=build /app/packages/core/dist /app/packages/core/dist
COPY --from=build /app/apps/cli/dist /app/apps/cli/dist
COPY --from=build /app/apps/web/.next /app/apps/web/.next
COPY --from=build /app/apps/web/public /app/apps/web/public
COPY --from=build /app/apps/docs/dist /app/apps/docs/dist
COPY --from=build /app/package.json /app/pnpm-workspace.yaml /app/
COPY --from=build /app/apps/web/package.json /app/apps/web/
COPY --from=build /app/apps/cli/package.json /app/apps/cli/
COPY --from=build /app/apps/docs/package.json /app/apps/docs/
COPY --from=build /app/packages/core/package.json /app/packages/core/
WORKDIR /app
VOLUME ["/data"]
EXPOSE 8787 8790 4321
CMD ["node", "apps/cli/dist/index.js", "worker"]
