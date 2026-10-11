#!/usr/bin/env bash
# Watch the Whisper endpoint until it is InService, then print how to test it.
set -uo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

ENDPOINT="${STT_ENDPOINT:-${PREFIX}-whisper}"
R="$DATA_REGION"

for i in $(seq 1 90); do
  info=$(aws sagemaker describe-endpoint --endpoint-name "$ENDPOINT" --region "$R" \
    --query '{Status:EndpointStatus,Reason:FailureReason}' --output json 2>/dev/null || echo '{}')
  status=$(printf '%s' "$info" | jq -r '.Status // "MISSING"')
  printf '\r  %s  status=%-12s (%ds elapsed)   ' "$ENDPOINT" "$status" $((i * 20))

  case "$status" in
    InService)
      printf '\n\n'
      hvi_log "endpoint is InService"
      aws sagemaker describe-endpoint --endpoint-name "$ENDPOINT" --region "$R" \
        --query 'ProductionVariants[0].{Variant:VariantName,Instance:CurrentInstanceCount,Type:CurrentServerlessConfig}' \
        --output table 2>/dev/null || true
      printf '\nTest it with:\n  ./.venv/bin/python tests/test_stt.py\n\n'
      exit 0
      ;;
    Failed)
      printf '\n\n'
      hvi_err "endpoint creation failed"
      printf '%s\n' "$info" | jq -r '.Reason'
      printf '\nRecent container logs:\n'
      aws logs tail "/aws/sagemaker/Endpoints/${ENDPOINT}" --region "$R" \
        --since 20m --format short 2>/dev/null | tail -25
      exit 1
      ;;
    MISSING)
      printf '\n'
      hvi_err "endpoint $ENDPOINT not found - run scripts/50-whisper.sh"
      exit 1
      ;;
  esac
  sleep 20
done

printf '\n'
hvi_warn "still not InService after 30 minutes; check the CloudWatch logs"
