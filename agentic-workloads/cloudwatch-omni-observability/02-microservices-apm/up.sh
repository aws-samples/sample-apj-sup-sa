#!/usr/bin/env bash
# Start the shop and the collector. The collector signs requests with credentials
# exported from your current AWS CLI session. They're short-lived: when they
# expire, re-run ./up.sh.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/.." && pwd)"
. "$ROOT/common/load-env.sh"
omni_load_env "$ROOT/.env"
unset -f omni_load_env
: "${AWS_REGION:?set AWS_REGION in .env}"

creds=$(aws configure export-credentials --format env) || exit 1
eval "$creds"
unset creds
cd "$HERE"
if [ -f "$ROOT/.env" ]; then
  docker compose --env-file "$ROOT/.env" up -d --build "$@"
else
  docker compose up -d --build "$@"
fi
echo
echo "frontend http://localhost:8000   orders http://localhost:8001   payments http://localhost:8002"
echo "collector logs:  docker compose logs -f otel-collector"
