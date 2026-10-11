#!/usr/bin/env bash
# Watch the OmniVoice endpoint come up, and say something useful when it does not.
#
# Container start does a pip install and a 3.3GB checkpoint download before the
# ping succeeds, so a long Creating is normal. What is NOT normal is a Failed,
# and the FailureReason plus the CloudWatch log is where the answer lives - both
# times the Whisper endpoint failed, the reason was in one of those two places.
#
# Usage
#   bash scripts/53-omnivoice-status.sh          # one look
#   bash scripts/53-omnivoice-status.sh --watch  # poll until terminal
#   bash scripts/53-omnivoice-status.sh --logs   # tail the container log

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

R="$DATA_REGION"
ENDPOINT="${PREFIX}-omnivoice"
MODE="${1:-once}"

show_logs() {
  local group="/aws/sagemaker/Endpoints/${ENDPOINT}"
  local stream
  stream=$(aws logs describe-log-streams --region "$R" --log-group-name "$group" \
    --order-by LastEventTime --descending --max-items 1 \
    --query 'logStreams[0].logStreamName' --output text 2>/dev/null || echo "")
  if [ -z "$stream" ] || [ "$stream" = "None" ]; then
    hvi_warn "no log stream yet under $group"
    return 0
  fi
  hvi_log "log stream: $stream"
  aws logs get-log-events --region "$R" --log-group-name "$group" \
    --log-stream-name "$stream" --limit 60 \
    --query 'events[].message' --output text 2>/dev/null | tail -50
}

describe() {
  aws sagemaker describe-endpoint --endpoint-name "$ENDPOINT" --region "$R" \
    --query '{Status:EndpointStatus,Reason:FailureReason,Variants:ProductionVariants[].{Type:CurrentInstanceType,Desired:DesiredInstanceCount,Current:CurrentInstanceCount}}' \
    --output json 2>/dev/null || echo '{"Status":"NONE"}'
}

case "$MODE" in
--logs)
  show_logs
  ;;
--watch)
  START=$(date +%s)
  while true; do
    OUT=$(describe)
    STATUS=$(jq -r '.Status' <<<"$OUT")
    printf '\r  %-12s  %4ds  ' "$STATUS" "$(( $(date +%s) - START ))"
    case "$STATUS" in
      InService)
        printf '\n'
        hvi_log "InService on $(jq -r '.Variants[0].Type // "?"' <<<"$OUT")"
        hvi_log "smoke test: ./.venv/bin/python tests/test_omnivoice.py"
        break ;;
      Failed)
        printf '\n'
        hvi_err "endpoint FAILED"
        jq -r '.Reason // "(no reason given)"' <<<"$OUT" >&2
        hvi_log "container log follows"
        show_logs
        exit 1 ;;
      NONE)
        printf '\n'; hvi_err "endpoint does not exist; run scripts/52-omnivoice.sh"; exit 1 ;;
    esac
    sleep 15
  done
  ;;
*)
  describe | jq .
  ;;
esac
