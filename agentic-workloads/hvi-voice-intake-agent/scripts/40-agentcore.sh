#!/usr/bin/env bash
# Build the AgentCore runtime container, push it to ECR, and register the runtime.
#
# AgentCore Runtime requires linux/arm64. This machine is Apple Silicon, so the
# build is native rather than emulated.

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

REPO="${PREFIX}-agent"
TAG="${AGENT_TAG:-latest}"
R="$DATA_REGION"
ECR="${AWS_ACCOUNT_ID}.dkr.ecr.${R}.amazonaws.com"
IMAGE="${ECR}/${REPO}:${TAG}"
RUNTIME_NAME="${PREFIX}_intake_agent"
AC_ROLE="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${PREFIX}-agentcore-role"

# ---- ECR repository ---------------------------------------------------------
if ! aws ecr describe-repositories --repository-names "$REPO" --region "$R" >/dev/null 2>&1; then
  hvi_log "creating ECR repository $REPO"
  aws ecr create-repository --repository-name "$REPO" --region "$R" \
    --image-scanning-configuration scanOnPush=true \
    --tags Key=Project,Value=health-voice-intake \
    --query 'repository.repositoryUri' --output text
else
  hvi_warn "ECR repository $REPO exists"
fi

# ---- Build context ----------------------------------------------------------
# common/ is shared with the Lambdas. Copying it in at build time keeps a single
# implementation of the checkpoint logic rather than two that can drift.
hvi_log "staging build context"
rm -rf agent/common && mkdir -p agent/common
# hvi_archive is imported lazily (inside submit_to_crm), so the guard below - which
# only sees top-level imports - cannot catch it being missing. Staged explicitly.
cp common/hvi_state.py common/hvi_prompts.py common/hvi_archive.py agent/common/

# Anything agent/ imports from common/ must be staged here, and forgetting it fails
# at container import time rather than now. The same omission took a live call down
# on the media host, so it is checked rather than trusted.
for mod in $(grep -rhoE '^(import|from) (hvi_[a-z_]+)' agent/*.py | awk '{print $2}' | sort -u); do
  if [ ! -f "agent/common/${mod}.py" ] && [ ! -f "agent/${mod}.py" ]; then
    hvi_err "agent/ imports ${mod} but it was not staged into agent/common/"
    hvi_err "add it to the cp above, or the container will fail at import."
    exit 1
  fi
done

hvi_log "logging in to ECR"
aws ecr get-login-password --region "$R" | docker login --username AWS --password-stdin "$ECR"

hvi_log "building linux/arm64 image (AgentCore requirement)"
docker buildx build \
  --platform linux/arm64 \
  -t "$IMAGE" \
  -f agent/Dockerfile \
  --push \
  agent/

hvi_log "pushed $IMAGE"
hvi_record "ecr.agent" "$IMAGE"

# ---- Register or update the AgentCore runtime -------------------------------
# A REDEPLOY MUST NOT SILENTLY CHANGE BEHAVIOUR.
#
# HVI_ALWAYS_FRESH used to default to 0 here, and it is not exported by config/,
# so any redeploy by anyone who had not set it in their shell turned demo mode
# OFF. That happened: three redeploys in one afternoon flipped it from 1 to 0
# without a word, and the next real call resumed a 19-hour-old session, skipped
# tobacco and alcohol because they were already answered on it, and looked to the
# caller like an interview that forgot to ask.
#
# So the deployed value is READ BACK and reused as the default. The flag now only
# changes when someone says so explicitly, and either way the value is printed.
CURRENT_FRESH=""
CURRENT_RUNTIME=$(aws bedrock-agentcore-control list-agent-runtimes --region "$R" \
  --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" \
  --output text 2>/dev/null || echo "None")
if [ -n "$CURRENT_RUNTIME" ] && [ "$CURRENT_RUNTIME" != "None" ]; then
  CURRENT_FRESH=$(aws bedrock-agentcore-control get-agent-runtime --region "$R" \
    --agent-runtime-id "$CURRENT_RUNTIME" \
    --query 'environmentVariables.HVI_ALWAYS_FRESH' --output text 2>/dev/null || echo "")
  [ "$CURRENT_FRESH" = "None" ] && CURRENT_FRESH=""
fi
FRESH="${HVI_ALWAYS_FRESH:-${CURRENT_FRESH:-0}}"
if [ -n "$CURRENT_FRESH" ] && [ "$FRESH" != "$CURRENT_FRESH" ]; then
  hvi_log "HVI_ALWAYS_FRESH CHANGING: $CURRENT_FRESH -> $FRESH (you set it explicitly)"
fi
if [ "$FRESH" = "0" ]; then
  hvi_log "HVI_ALWAYS_FRESH=0: resume is ON. A repeat caller continues their"
  hvi_log "  previous unfinished session. Correct for production, confusing in a"
  hvi_log "  demo - set HVI_ALWAYS_FRESH=1 to start every call clean."
else
  hvi_log "HVI_ALWAYS_FRESH=$FRESH: DEMO MODE. Every inbound call starts a new"
  hvi_log "  questionnaire and DISCARDS answers already collected. Never for production."
fi

# THE ARCHIVE VARS ARE NOT OPTIONAL DECORATION.
#
# hvi_archive reads CALL_ARCHIVE_BUCKET at import and treats an empty value as
# "archiving is off" - it returns skipped, never raises, and logs one line. So
# omitting it here did not fail anything; it just meant that on EVERY deployed
# call the transcript was never written to S3, set_archive_uris was never called,
# and Salesforce's Transcript_URI__c / Recording_URI__c were saved blank. The
# module, the bucket, the IAM grant and the CRM fields were all correct and in
# place - the one missing link was that the runtime could not see the bucket name.
#
# scripts/55-call-archive.sh ends by telling you to run this script, which is only
# true now that these are actually passed.
ENV_JSON=$(jq -nc \
  --arg dr "$DATA_REGION" \
  --arg model "$BEDROCK_MODEL_ID" \
  --arg os "$OPENSEARCH_ENDPOINT" \
  --arg graph "${PREFIX}-graph" \
  --arg crm "${PREFIX}-crm-writeback" \
  --arg email "${PREFIX}-send-confirmation" \
  --arg cs "$TBL_CALL_STATE" \
  --arg cse "$TBL_CALL_SESSION" \
  --arg crmt "$TBL_CRM" \
  --arg fs "$TBL_FORM_SCHEMA" \
  --arg rt "$TBL_RESPONSES" \
  --arg fresh "$FRESH" \
  --arg arcb "${CALL_ARCHIVE_BUCKET:-}" \
  --arg arcp "${CALL_ARCHIVE_PREFIX:-calls}" \
  --arg audb "${AUDIO_BUCKET:-}" \
  '{
     DATA_REGION:$dr, BEDROCK_MODEL_ID:$model, OPENSEARCH_ENDPOINT:$os,
     GRAPH_FN:$graph, CRM_FN:$crm, EMAIL_FN:$email,
     TBL_CALL_STATE:$cs, TBL_CALL_SESSION:$cse, TBL_CRM:$crmt,
     TBL_FORM_SCHEMA:$fs, TBL_RESPONSES:$rt, HVI_ALWAYS_FRESH:$fresh,
     CALL_ARCHIVE_BUCKET:$arcb, CALL_ARCHIVE_PREFIX:$arcp, AUDIO_BUCKET:$audb
   }')

if [ -n "${CALL_ARCHIVE_BUCKET:-}" ]; then
  hvi_log "call archive: s3://${CALL_ARCHIVE_BUCKET}/${CALL_ARCHIVE_PREFIX:-calls}/"
else
  hvi_warn "CALL_ARCHIVE_BUCKET is empty - the deployed agent will NOT archive"
  hvi_warn "  transcripts and Salesforce Transcript_URI__c will stay blank."
  hvi_warn "  Run: bash scripts/55-call-archive.sh"
fi

EXISTING=$(aws bedrock-agentcore-control list-agent-runtimes --region "$R" \
  --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" \
  --output text 2>/dev/null || echo "None")

if [ "$EXISTING" = "None" ] || [ -z "$EXISTING" ]; then
  hvi_log "creating AgentCore runtime $RUNTIME_NAME"
  aws bedrock-agentcore-control create-agent-runtime \
    --agent-runtime-name "$RUNTIME_NAME" \
    --description "Health insurance medical-history intake agent" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${IMAGE}\"}}" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --role-arn "$AC_ROLE" \
    --environment-variables "$ENV_JSON" \
    --region "$R" \
    --output json | jq '{agentRuntimeId, agentRuntimeArn, status}'
else
  hvi_log "updating AgentCore runtime $RUNTIME_NAME ($EXISTING)"
  aws bedrock-agentcore-control update-agent-runtime \
    --agent-runtime-id "$EXISTING" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${IMAGE}\"}}" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --role-arn "$AC_ROLE" \
    --environment-variables "$ENV_JSON" \
    --region "$R" \
    --output json | jq '{agentRuntimeId, status}'
fi

hvi_log "waiting for the runtime to become READY"
for i in $(seq 1 40); do
  info=$(aws bedrock-agentcore-control list-agent-runtimes --region "$R" \
    --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'] | [0]" --output json)
  status=$(printf '%s' "$info" | jq -r '.status // "UNKNOWN"')
  rid=$(printf '%s' "$info" | jq -r '.agentRuntimeId // empty')
  printf '\r  status=%s (%ds)   ' "$status" $((i * 10)) >&2
  if [ "$status" = "READY" ]; then
    printf '\n' >&2
    arn=$(printf '%s' "$info" | jq -r '.agentRuntimeArn // empty')
    if [ -z "$rid" ] || [ -z "$arn" ] || [[ "$arn" != */"$rid" ]]; then
      hvi_err "READY runtime returned an invalid ID/ARN pair"
      printf '%s\n' "$info" | jq '.'
      exit 1
    fi

    hvi_log "runtime READY: $rid"
    local_file="config/env.local.sh"
    tmp=$(mktemp "${local_file}.tmp.XXXXXX")
    trap 'rm -f "$tmp"' EXIT
    if [ -f "$local_file" ]; then
      # Preserve the original mode while replacing only these exact export keys.
      cp -p "$local_file" "$tmp"
      : > "$tmp"
      while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
          "export AGENT_RUNTIME_ID="*|"export AGENT_RUNTIME_ARN="*) ;;
          *) printf '%s\n' "$line" >> "$tmp" ;;
        esac
      done < "$local_file"
    fi
    {
      printf 'export AGENT_RUNTIME_ID="%s"\n' "$rid"
      printf 'export AGENT_RUNTIME_ARN="%s"\n' "$arn"
    } >> "$tmp"
    mv "$tmp" "$local_file"
    hvi_log "updated AGENT_RUNTIME_ID / ARN in $local_file"
    exit 0
  fi
  if [ "$status" = "CREATE_FAILED" ] || [ "$status" = "UPDATE_FAILED" ]; then
    printf '\n' >&2
    hvi_err "runtime entered $status"
    printf '%s\n' "$info" | jq '.'
    exit 1
  fi
  sleep 10
done

printf '\n' >&2
hvi_warn "runtime not READY after 400s; check with: aws bedrock-agentcore-control list-agent-runtimes --region $R"
