#!/bin/bash
# Wrapper around scripts/api-call.py for human-friendly user usage lookups.
#
# Usage:
#   query-user-usage.sh me                                 # /usage/me
#   query-user-usage.sh me history [period]                # /usage/me/history?period=...
#   query-user-usage.sh user <username> [history [period]] # /usage/<username>[/history?period=...]
#   query-user-usage.sh top [metric] [period] [limit]      # /usage/top?...
#   query-user-usage.sh by-model [period]                  # /usage/by-model?period=...
#
# Env:
#   BEDROCK_METER_API_ENDPOINT  https://<api-id>.execute-api.<region>.amazonaws.com (required)
#   AWS_REGION                  defaults to ap-northeast-2
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CALL="$SCRIPT_DIR/api-call.py"

if [ -z "${BEDROCK_METER_API_ENDPOINT:-}" ]; then
  echo "BEDROCK_METER_API_ENDPOINT env var required" >&2
  exit 1
fi

cmd="${1:-me}"
case "$cmd" in
  me)
    if [ "${2:-}" = "history" ]; then
      "$CALL" GET "/usage/me/history?period=${3:-month}"
    else
      "$CALL" GET /usage/me
    fi
    ;;
  user)
    name="${2:?username required}"
    if [ "${3:-}" = "history" ]; then
      "$CALL" GET "/usage/$name/history?period=${4:-month}"
    else
      "$CALL" GET "/usage/$name"
    fi
    ;;
  top)
    metric="${2:-totalTokens}"
    period="${3:-month}"
    limit="${4:-5}"
    "$CALL" GET "/usage/top?metric=$metric&period=$period&limit=$limit"
    ;;
  by-model)
    period="${2:-month}"
    "$CALL" GET "/usage/by-model?period=$period"
    ;;
  *)
    sed -n '2,/^set -euo/p' "$0" | sed 's/^# \?//' | head -n -1 >&2
    exit 1
    ;;
esac
