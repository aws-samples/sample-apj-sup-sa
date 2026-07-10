#!/usr/bin/env sh
set -eu

: "${BEDROCK_REGION:?missing required environment variable}"
: "${GATEWAY_DB_HOST:?missing required environment variable}"
: "${GATEWAY_DB_NAME:?missing required environment variable}"
: "${GATEWAY_PUBLIC_URL:?missing required environment variable}"
: "${OIDC_CLIENT_ID:?missing required environment variable}"
: "${OIDC_ISSUER:?missing required environment variable}"

GATEWAY_CONFIG_PATH="${GATEWAY_CONFIG_PATH:-/tmp/claude-gateway/gateway.yaml}"
GATEWAY_DB_PORT="${GATEWAY_DB_PORT:-5432}"
CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-/tmp/.claude}"

mkdir -p "$(dirname "$GATEWAY_CONFIG_PATH")" "$CLAUDE_CONFIG_DIR"
umask 077

cat > "$GATEWAY_CONFIG_PATH" <<'YAML'
listen:
  host: 0.0.0.0
  port: 8080
  public_url: ${GATEWAY_PUBLIC_URL}

oidc:
  issuer: ${OIDC_ISSUER}
  client_id: ${OIDC_CLIENT_ID}
  client_secret: ${OIDC_CLIENT_SECRET}
  scopes: [openid, email, profile]
YAML

if [ -n "${OIDC_ALLOWED_EMAIL_DOMAINS:-}" ]; then
  # Comma-joined list -> YAML flow sequence; YAML trims whitespace around items.
  printf "  allowed_email_domains: [%s]\n" "$OIDC_ALLOWED_EMAIL_DOMAINS" >> "$GATEWAY_CONFIG_PATH"
fi

cat >> "$GATEWAY_CONFIG_PATH" <<'YAML'

session:
  jwt_secret: ${GATEWAY_JWT_SECRET}
  ttl_hours: 1

store:
  postgres_url: postgresql://${GATEWAY_DB_HOST}:${GATEWAY_DB_PORT}/${GATEWAY_DB_NAME}?sslmode=require
  username: ${GATEWAY_DB_USERNAME}
  password: ${GATEWAY_DB_PASSWORD}

upstreams:
  - provider: bedrock
    region: ${BEDROCK_REGION}
    auth: {}

auto_include_builtin_models: true
YAML

if [ "${1:-}" = "--render-only" ]; then
  cat "$GATEWAY_CONFIG_PATH"
  exit 0
fi

exec /usr/local/bin/claude gateway --config "$GATEWAY_CONFIG_PATH"
