#!/usr/bin/env bash
# Switch the reasoning model on the deployed AgentCore runtime.
#
# The choice is a data-residency trade-off, not a quality ranking. Probed with
# tests/diag_residency.py against ap-south-1:
#
#   inregion  mistral.mistral-large-3-675b-instruct
#             Inference stays in ap-south-1, so DPDP data localisation holds.
#             Weaker at following a multi-step tool protocol - observed skipping
#             save_answer and get_followup_questions mid-call.
#
#   apac      apac.anthropic.claude-3-7-sonnet-20250219-v1:0
#             Routes across the APAC region set, so inference LEAVES India.
#             Materially better at holding the tool protocol.
#
#   global    global.anthropic.claude-sonnet-4-5-20250929-v1:0
#             Routes worldwide. Best quality, worst residency story. Only for a
#             demo where DPDP is explicitly out of scope.
#
#   haiku     anthropic.claude-3-haiku-20240307-v1:0
#             In-region and cheap, but the weakest of the four.
#
# Usage:
#   bash scripts/41-agent-model.sh apac
#   bash scripts/41-agent-model.sh inregion

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

CHOICE="${1:-inregion}"

case "$CHOICE" in
  inregion) MODEL="mistral.mistral-large-3-675b-instruct" ; RESIDENCY="ap-south-1 only (DPDP compliant)" ;;
  apac)     MODEL="apac.anthropic.claude-3-7-sonnet-20250219-v1:0" ; RESIDENCY="APAC region set (leaves India)" ;;
  global)   MODEL="global.anthropic.claude-sonnet-4-5-20250929-v1:0" ; RESIDENCY="worldwide (NOT DPDP compliant)" ;;
  haiku)    MODEL="anthropic.claude-3-haiku-20240307-v1:0" ; RESIDENCY="ap-south-1 only (DPDP compliant)" ;;
  *)
    hvi_err "unknown choice '$CHOICE'. Use: inregion | apac | global | haiku"
    exit 2
    ;;
esac

: "${AGENT_RUNTIME_ID:?AGENT_RUNTIME_ID not set - run scripts/40-agentcore.sh first}"

R="$DATA_REGION"
IMAGE="${AWS_ACCOUNT_ID}.dkr.ecr.${R}.amazonaws.com/${PREFIX}-agent:${AGENT_TAG:-latest}"
AC_ROLE="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${PREFIX}-agentcore-role"

hvi_log "model:     $MODEL"
hvi_log "residency: $RESIDENCY"
hvi_log "runtime:   $AGENT_RUNTIME_ID"

# update-agent-runtime REPLACES the whole environment map, so anything missing
# from ENV_JSON is DELETED from the runtime. This script is "switch the model", and
# it was silently doing more than that: HVI_ALWAYS_FRESH was absent here, so
# changing model reset demo mode to the default, and the archive variables were
# absent too, so a model switch would have un-done scripts/40-agentcore.sh's
# archiving. Both are carried across explicitly now.
#
# HVI_ALWAYS_FRESH is READ BACK from the deployed runtime for the same reason
# scripts/40-agentcore.sh reads it back: it is not exported by config/, so
# defaulting it here would flip resume behaviour for anyone who had not set it in
# their shell. An explicit value in the environment still wins.
CURRENT_FRESH=$(aws bedrock-agentcore-control get-agent-runtime --region "$R" \
  --agent-runtime-id "$AGENT_RUNTIME_ID" \
  --query 'environmentVariables.HVI_ALWAYS_FRESH' --output text 2>/dev/null || echo "")
[ "$CURRENT_FRESH" = "None" ] && CURRENT_FRESH=""
FRESH="${HVI_ALWAYS_FRESH:-${CURRENT_FRESH:-0}}"
hvi_log "resume:    HVI_ALWAYS_FRESH=$FRESH (preserved, not reset by this script)"

ENV_JSON=$(jq -nc \
  --arg dr    "$DATA_REGION" \
  --arg model "$MODEL" \
  --arg os    "$OPENSEARCH_ENDPOINT" \
  --arg graph "${PREFIX}-graph" \
  --arg crm   "${PREFIX}-crm-writeback" \
  --arg email "${PREFIX}-send-confirmation" \
  --arg cs    "$TBL_CALL_STATE" \
  --arg cse   "$TBL_CALL_SESSION" \
  --arg crmt  "$TBL_CRM" \
  --arg fs    "$TBL_FORM_SCHEMA" \
  --arg rt    "$TBL_RESPONSES" \
  --arg fresh "$FRESH" \
  --arg arcb  "${CALL_ARCHIVE_BUCKET:-}" \
  --arg arcp  "${CALL_ARCHIVE_PREFIX:-calls}" \
  --arg audb  "${AUDIO_BUCKET:-}" \
  '{
     DATA_REGION:$dr, BEDROCK_MODEL_ID:$model, OPENSEARCH_ENDPOINT:$os,
     GRAPH_FN:$graph, CRM_FN:$crm, EMAIL_FN:$email,
     TBL_CALL_STATE:$cs, TBL_CALL_SESSION:$cse, TBL_CRM:$crmt,
     TBL_FORM_SCHEMA:$fs, TBL_RESPONSES:$rt, HVI_ALWAYS_FRESH:$fresh,
     CALL_ARCHIVE_BUCKET:$arcb, CALL_ARCHIVE_PREFIX:$arcp, AUDIO_BUCKET:$audb
   }')

aws bedrock-agentcore-control update-agent-runtime \
  --agent-runtime-id "$AGENT_RUNTIME_ID" \
  --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${IMAGE}\"}}" \
  --network-configuration '{"networkMode":"PUBLIC"}' \
  --role-arn "$AC_ROLE" \
  --environment-variables "$ENV_JSON" \
  --region "$R" \
  --output json | jq '{agentRuntimeId, status}'

hvi_log "waiting for the runtime to become READY"
for i in $(seq 1 30); do
  status=$(aws bedrock-agentcore-control list-agent-runtimes --region "$R" \
    --query "agentRuntimes[?agentRuntimeId=='${AGENT_RUNTIME_ID}'].status | [0]" \
    --output text 2>/dev/null || echo UNKNOWN)
  printf '\r  status=%s (%ds)   ' "$status" $((i * 8)) >&2
  if [ "$status" = "READY" ]; then
    printf '\n' >&2
    hvi_log "runtime READY on $MODEL"
    exit 0
  fi
  if [[ "$status" == *FAILED* ]]; then
    printf '\n' >&2
    hvi_err "runtime entered $status"
    exit 1
  fi
  sleep 8
done

printf '\n' >&2
hvi_warn "not READY after 240s - check scripts/status.sh"
