#!/usr/bin/env bash
# Force-unblock through the authenticated Core API so IAM policy and STATE stay reconciled.
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <username|user-arn> [reason]"
  echo "Requires BEDROCK_METER_API_ENDPOINT and admin AWS credentials."
  exit 1
fi

TARGET="$1"
USERNAME="${TARGET##*/}"
REASON="${2:-manual operational recovery}"

if [[ ! "$USERNAME" =~ ^[A-Za-z0-9+=,.@_-]{1,64}$ ]]; then
  echo "ERROR: invalid path-less IAM username: $USERNAME" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/api-call.py" POST "/admin/unblock/$USERNAME" \
  "{\"reason\":$(printf '%s' "$REASON" | jq -Rs .)}"
