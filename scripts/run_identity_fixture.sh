#!/bin/sh
# Isolated issue-11 browser qualification only. Never use these fixture secrets elsewhere.
set -eu
export VERDANT_DATABASE_URL='postgresql+psycopg://postgres:verdant-test-only@127.0.0.1:55431/verdant_issue11_workspace'
export VERDANT_OIDC_ISSUER='http://127.0.0.1:18081/realms/verdant-qualification'
export VERDANT_OIDC_CLIENT_ID=verdant-browser
export VERDANT_OIDC_CLIENT_SECRET=verdant-test-only-browser-secret
export VERDANT_APP_URL='http://127.0.0.1:18787'
export VERDANT_OIDC_GROUP=/workspace
export VERDANT_API_AUDIENCE=verdant-api
export VERDANT_SERVICE_CLIENTS=verdant-worker
export VERDANT_INSECURE_LOCAL_HTTP=1
uv run alembic upgrade head
exec uv run uvicorn verdant.api:create_app --factory --host 127.0.0.1 --port 8788
