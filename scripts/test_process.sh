#!/bin/sh
# Explicit owned 191xx fixture; never reads a default/shared database target.
set -eu
export VERDANT_TEST_DATABASE_URL='postgresql+psycopg://postgres:process-e2e-database@127.0.0.1:19132/verdant_process_test'
export VERDANT_STORAGE_TEST_ENDPOINT='http://127.0.0.1:19133'
export VERDANT_STORAGE_TEST_BUCKET='verdant-artifacts'
export VERDANT_STORAGE_TEST_LOCAL_HTTP=1
export AWS_ACCESS_KEY_ID=browser-e2e
export AWS_SECRET_ACCESS_KEY=browser-e2e-storage
export DATACHAIN_NO_ANALYTICS=1
exec uv run --locked --group workflows pytest "$@"
