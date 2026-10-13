#!/bin/bash
# Fetch a Cognito access token for the AgentCore Gateway (MCP).
#
# Required env vars (set after `cdk deploy`):
#   AWS_ACCOUNT             — 12-digit account ID
#   COGNITO_DOMAIN_PREFIX   — e.g. internal-agent-mcp-${AWS_ACCOUNT}
#   CLIENT_ID               — Cognito App Client ID (from CDK output / SSM)
#   CLIENT_SECRET           — Cognito App Client Secret
#   AWS_REGION              — defaults to ap-northeast-1
#
# Usage (Claude Code header-command):
#   claude mcp add --transport http internal-tools \
#     "https://<GATEWAY_URL>/mcp" \
#     --header-command "AWS_ACCOUNT=... COGNITO_DOMAIN_PREFIX=... CLIENT_ID=... CLIENT_SECRET=... ./get-gateway-token.sh"

set -euo pipefail

: "${AWS_ACCOUNT:?AWS_ACCOUNT is required}"
: "${COGNITO_DOMAIN_PREFIX:?COGNITO_DOMAIN_PREFIX is required (e.g. internal-agent-mcp-${AWS_ACCOUNT})}"
: "${CLIENT_ID:?CLIENT_ID is required}"
: "${CLIENT_SECRET:?CLIENT_SECRET is required}"
REGION="${AWS_REGION:-ap-northeast-1}"

TOKEN=$(curl -s -X POST \
  "https://${COGNITO_DOMAIN_PREFIX}.auth.${REGION}.amazoncognito.com/oauth2/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=${CLIENT_ID}&client_secret=${CLIENT_SECRET}&scope=mcp/access" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo "{\"Authorization\": \"Bearer $TOKEN\"}"
