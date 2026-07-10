#!/usr/bin/env bash
#
# Generate traffic against the deployed sample app so the planted bugs show up
# in CloudWatch logs and X-Ray, ready for Voice to debug.
#
# Usage:
#   ./generate-traffic.sh                 # reads api_url from terraform output
#   ./generate-traffic.sh https://abc123.execute-api.us-east-1.amazonaws.com
#   API_URL=https://... ./generate-traffic.sh
#
# Note: most endpoints return HTTP 500 on purpose. That is the point: it seeds
# the logs with the planted errors. POST /users is the working control.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

API_URL="${1:-${API_URL:-}}"
if [ -z "$API_URL" ]; then
  API_URL="$(cd "$SCRIPT_DIR" && terraform output -raw api_url 2>/dev/null || true)"
fi

if [ -z "$API_URL" ]; then
  echo "Could not determine the API URL." >&2
  echo "Run 'terraform apply' first, or pass the URL: ./generate-traffic.sh https://..." >&2
  exit 1
fi

API_URL="${API_URL%/}"
echo
echo "Target: $API_URL"
echo

req() {
  local method="$1" path="$2" data="${3:-}"
  echo "> $method $path"
  if [ -n "$data" ]; then
    curl -s -o /tmp/voice_traffic_body -w "  HTTP %{http_code}\n" \
      -X "$method" "$API_URL$path" \
      -H 'Content-Type: application/json' -d "$data"
  else
    curl -s -o /tmp/voice_traffic_body -w "  HTTP %{http_code}\n" \
      -X "$method" "$API_URL$path"
  fi
  echo "  $(cat /tmp/voice_traffic_body)"
  echo
}

# 1. POST /users — working control. Seeds a couple of users and clean logs.
echo "--- WORKING"
req POST /users '{"name":"Carol","email":"carol@example.com"}'
req POST /users '{"name":"Dave","email":"dave@example.com"}'

echo
echo "--- PLANTED BUGS"
# 2. GET /users — bug: maps item.userId, table key is user_id.
for _ in 1 2 3; do req GET /users; done

# 3. GET /users/{id} — bug: reads pathParameters.userId, route param is id.
for id in u-001 u-002 u-003; do req GET "/users/$id"; done

# 4. DELETE /users/{id} — bug: role lacks dynamodb:DeleteItem.
for id in u-001 u-002; do req DELETE "/users/$id"; done

# 5. GET /stats — bug: reads DYNAMO_TABLE, configured with TABLE_NAME.
for _ in 1 2 3; do req GET /stats; done

rm -f /tmp/voice_traffic_body

echo
echo "--- DONE"
echo "The 500s were expected."
echo "Now run the bot and ask it about an endpoint."
