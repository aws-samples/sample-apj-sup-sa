#!/usr/bin/env bash
# Deploy a tier to a throwaway sandbox stack, assert the rendered dashboard
# matches the tier. Usage: scripts/deploy_and_verify.sh <tier> <region>
set -euo pipefail
TIER="${1:?tier 1|2|3}"; REGION="${2:-us-east-1}"
STACK="bedrock-fm-dashboard-test"
DASH="Bedrock-FM-Dashboard-Test"
LOGGRP="${SANDBOX_LOG_GROUP:-}"
CTGRP="${SANDBOX_CT_GROUP:-}"

if [[ "$TIER" -ge 2 && -z "$LOGGRP" ]]; then echo "SANDBOX_LOG_GROUP must be set for tier>=2"; exit 2; fi
if [[ "$TIER" -ge 3 && -z "$CTGRP" ]]; then echo "SANDBOX_CT_GROUP must be set for tier 3"; exit 2; fi

PARAMS=(DashboardName="$DASH")
[[ "$TIER" -ge 2 ]] && PARAMS+=(ModelInvocationLogGroup="$LOGGRP")
[[ "$TIER" -ge 3 ]] && PARAMS+=(CloudTrailLogGroup="$CTGRP")

aws cloudformation deploy --template-file cloudformation/dashboard.yaml \
  --stack-name "$STACK" --region "$REGION" --no-fail-on-empty-changeset \
  --parameter-overrides "${PARAMS[@]}"

TIER_OUT=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='DeployedTier'].OutputValue" --output text)
echo "DeployedTier output = $TIER_OUT (expected $TIER)"
[[ "$TIER_OUT" == "$TIER" ]] || { echo "TIER MISMATCH"; exit 1; }

BODY=$(aws cloudwatch get-dashboard --dashboard-name "$DASH" --region "$REGION" \
  --query DashboardBody --output text)
HAS6=$(echo "$BODY" | grep -c "6. Per-tenant attribution" || true)
HAS7=$(echo "$BODY" | grep -c "7. Identity, routing" || true)
echo "Section6 present=$HAS6 Section7 present=$HAS7"
case "$TIER" in
  1) [[ "$HAS6" -eq 0 && "$HAS7" -eq 0 ]] || { echo "FAIL tier1"; exit 1; } ;;
  2) [[ "$HAS6" -ge 1 && "$HAS7" -eq 0 ]] || { echo "FAIL tier2"; exit 1; } ;;
  3) [[ "$HAS6" -ge 1 && "$HAS7" -ge 1 ]] || { echo "FAIL tier3"; exit 1; } ;;
esac
echo "TIER $TIER OK"
