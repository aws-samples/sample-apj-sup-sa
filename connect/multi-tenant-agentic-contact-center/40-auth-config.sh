#!/bin/sh
# Runs via the nginx image entrypoint (/docker-entrypoint.d) before nginx starts.
# Writes the SPA runtime config to /auth-config.json.
#
# Preferred: RUNTIME_CONFIG is a full JSON document injected from an SSM
# parameter (ECS secret) — used by the modular "v2" stacks, where the connect
# stack merges its values into the same parameter. Written verbatim.
#
# Fallback: build the file from individual Cognito/Connect env vars (used by the
# original single-stack deployment).
set -e

OUT=/usr/share/nginx/html/auth-config.json

if [ -n "${RUNTIME_CONFIG:-}" ]; then
  printf '%s' "${RUNTIME_CONFIG}" > "${OUT}"
  echo "[entrypoint] wrote ${OUT} from RUNTIME_CONFIG (SSM)"
else
  cat > "${OUT}" <<EOF
{
  "region": "${COGNITO_REGION:-}",
  "userPoolId": "${COGNITO_USER_POOL_ID:-}",
  "clientId": "${COGNITO_CLIENT_ID:-}",
  "domain": "${COGNITO_DOMAIN:-}",
  "connect": {
    "ccpUrl": "${CONNECT_CCP_URL:-}",
    "region": "${CONNECT_REGION:-}",
    "casesApiUrl": "${CONNECT_CASES_API_URL:-}"
  }
}
EOF
  echo "[entrypoint] wrote ${OUT} from env vars (fallback)"
fi
