#!/usr/bin/env bash
# Clone/pin Trigger.dev official self-host compose and start it.
# When run via the Compose `trigger` service, VERDANT_HOST_ROOT must be the
# absolute host path to this repo so nested volume mounts resolve on the host.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Prefer host path (for docker-from-docker); fall back to script location.
ROOT="${VERDANT_HOST_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
PLATFORM_DIR="$ROOT/infra/trigger/platform"
TAG="${TRIGGER_IMAGE_TAG:-v4.0.0}"
COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-verdant}"
# Verdant host-port cluster (187xx) — keep Trigger with the rest of the stack
TRIGGER_HOST_PORT="${TRIGGER_HOST_PORT:-18704}"
TRIGGER_ORIGIN="http://localhost:${TRIGGER_HOST_PORT}"

mkdir -p "$(dirname "$PLATFORM_DIR")"

if [[ ! -d "$PLATFORM_DIR/.git" ]]; then
  echo "Cloning Trigger.dev hosting/docker (shallow)…"
  rm -rf "$PLATFORM_DIR"
  git clone --depth=1 --filter=blob:none --sparse \
    https://github.com/triggerdotdev/trigger.dev.git "$PLATFORM_DIR"
  (
    cd "$PLATFORM_DIR"
    git sparse-checkout set hosting/docker
  )
fi

HOSTING="$PLATFORM_DIR/hosting/docker"
if [[ ! -f "$HOSTING/.env" ]]; then
  cp "$HOSTING/.env.example" "$HOSTING/.env"
fi

# Pin image tag (portable; avoid GNU sed -i)
if grep -q '^TRIGGER_IMAGE_TAG=' "$HOSTING/.env"; then
  tmp="$(mktemp)"
  sed "s/^TRIGGER_IMAGE_TAG=.*/TRIGGER_IMAGE_TAG=${TAG}/" "$HOSTING/.env" > "$tmp"
  mv "$tmp" "$HOSTING/.env"
else
  echo "TRIGGER_IMAGE_TAG=${TAG}" >> "$HOSTING/.env"
fi

ensure_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$HOSTING/.env"; then
    tmp="$(mktemp)"
    sed "s|^${key}=.*|${key}=${val}|" "$HOSTING/.env" > "$tmp"
    mv "$tmp" "$HOSTING/.env"
  else
    echo "${key}=${val}" >> "$HOSTING/.env"
  fi
}

ensure_env APP_ORIGIN "$TRIGGER_ORIGIN"
ensure_env LOGIN_ORIGIN "$TRIGGER_ORIGIN"
ensure_env API_ORIGIN "$TRIGGER_ORIGIN"
ensure_env DEV_OTEL_EXPORTER_OTLP_ENDPOINT "${TRIGGER_ORIGIN}/otel"
ensure_env DOCKER_REGISTRY_URL "localhost:18714"

# Local-dev auth: magic link for verdant@example.com auto-completes (no email / no signup form).
ensure_env NODE_ENV "development"
ensure_env APP_ENV "development"
ensure_env ADMIN_EMAILS "^verdant@example\\.com$"

WEBAPP_COMPOSE="$HOSTING/webapp/docker-compose.yml"

# Remap published host ports into the Verdant 187xx cluster (idempotent).
remap_host_port() {
  local file="$1" from="$2" to="$3"
  if grep -q ":${from}:" "$file" || grep -qE ":${from}\$" "$file"; then
    tmp="$(mktemp)"
    # Match host:container forms like 0.0.0.0:8030:3000 or 127.0.0.1:5433:5432
    sed -E "s/(:)${from}(:)/\\1${to}\\2/g" "$file" > "$tmp"
    mv "$tmp" "$file"
    echo "Remapped Trigger host port ${from} → ${to}"
  fi
}

# Public dashboard
remap_host_port "$WEBAPP_COMPOSE" 8030 "$TRIGGER_HOST_PORT"
# Already-remapped dashboard from a prior Verdant run (keep in sync if env changes)
if ! grep -q ":${TRIGGER_HOST_PORT}:3000" "$WEBAPP_COMPOSE"; then
  # Catch older Verdant mapping if present
  remap_host_port "$WEBAPP_COMPOSE" 18704 "$TRIGGER_HOST_PORT"
fi

# Infra (bound to 127.0.0.1 in upstream) — keep them in-cluster too
remap_host_port "$WEBAPP_COMPOSE" 5433 18710
remap_host_port "$WEBAPP_COMPOSE" 6389 18711
remap_host_port "$WEBAPP_COMPOSE" 9123 18712
# ClickHouse native: stock 9090, or prior Verdant 9010
if grep -q '9090:9000' "$WEBAPP_COMPOSE"; then
  remap_host_port "$WEBAPP_COMPOSE" 9090 18713
elif grep -q '9010:9000' "$WEBAPP_COMPOSE"; then
  remap_host_port "$WEBAPP_COMPOSE" 9010 18713
else
  remap_host_port "$WEBAPP_COMPOSE" 18713 18713
fi
remap_host_port "$WEBAPP_COMPOSE" 5000 18714
remap_host_port "$WEBAPP_COMPOSE" 9000 18715
remap_host_port "$WEBAPP_COMPOSE" 9001 18716

echo "Starting Trigger.dev (webapp + worker) from $HOSTING …"
(
  cd "$HOSTING"
  docker compose -p "${COMPOSE_PROJECT}-trigger" \
    -f webapp/docker-compose.yml \
    -f worker/docker-compose.yml \
    up -d
)

echo "Trigger.dev dashboard: ${TRIGGER_ORIGIN}"
echo "Login: enter verdant@example.com (instant — no email needed in local NODE_ENV=development)."
echo "Set TRIGGER_API_URL=${TRIGGER_ORIGIN} and TRIGGER_SECRET_KEY from the dashboard in Verdant .env if you use Trigger tasks."
