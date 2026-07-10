#!/usr/bin/env bash
#
# deploy_backend.sh — one-shot backend bring-up for the Voice Analytics Agent.
#
# Packages the in-repo agent + UI (app/agentcore_strands, app/ui), uploads the
# artifacts to S3, deploys the demo CloudFormation stack, then writes
# app/voice/.env with the live stack outputs so `uv run bot.py` just works.
#
# The voice bot is only a front-end; it invokes the deployed Strands agent on
# Bedrock AgentCore. This script is what makes that backend exist. See AGENTS.md.
#
# Usage:
#   infrastructure/scripts/deploy_backend.sh                # defaults below
#   BUCKET=my-bucket infrastructure/scripts/deploy_backend.sh
#   infrastructure/scripts/deploy_backend.sh --recreate     # force delete + recreate (DESTROYS Aurora data)
#   infrastructure/scripts/deploy_backend.sh --skip-package # reuse artifacts already in S3 (faster re-deploy)
#   infrastructure/scripts/deploy_backend.sh --agent-only   # fast path: re-zip agent code, rebuild image, update runtime (~5-8 min)
#   infrastructure/scripts/deploy_backend.sh --voice-only   # fast path: re-zip app/voice, rebuild voice image, update the voice runtime (~6-12 min)
#
# --agent-only / --voice-only skip CloudFormation entirely. Use --agent-only when you
# only changed files under app/agentcore_strands/, --voice-only for app/voice/. Each:
#   (1) re-zips and uploads the code to the CodeBuild source key in S3
#   (2) triggers the existing CodeBuild project to rebuild + push the Docker image
#   (3) calls update-agent-runtime (same ECR :latest URI, forces re-pull) → new version,
#       re-sending authorizer + header allowlist + network config so they don't reset
#   (4) waits for the new version to reach READY, then updates the endpoint
#
# Env overrides: BUCKET, STACK, REGION, ENV_NAME, DEMO_ROLE (rental_admin|analyst|staff|saas_admin)
#
# Frontend: option 1 (Pipecat Playground) needs no build — after this finishes,
#   `cd app/voice && uv run bot.py --transport daily` and open the printed URL.

set -euo pipefail

# ── Config (override via env) ────────────────────────────────────────────────
BUCKET="${BUCKET:-agentic-analytics-demo}"
STACK="${STACK:-agentic-analytics-demo}"
REGION="${REGION:-us-west-2}"
DEMO_ROLE="${DEMO_ROLE:-rental_admin}"   # rental_admin has full access incl. create_booking

# Voice (optional, additive). Set ENABLE_VOICE=true VOICE_MODE=agentcore plus
# DEEPGRAM_API_KEY (read from app/voice/.env if unset) to deploy the voice AgentCore
# Runtime (WebRTC + KVS TURN) alongside the backend. Default: backend only (no voice).
ENABLE_VOICE="${ENABLE_VOICE:-false}"
VOICE_MODE="${VOICE_MODE:-agentcore}"

RECREATE=false
SKIP_PACKAGE=false
AGENT_ONLY=false
VOICE_ONLY=false
# No-rollback is ON by default: a failed create is left as CREATE_FAILED (resources
# intact, via --disable-rollback) so it can be fixed forward with update-stack instead
# of a full teardown+recreate. Pass --rollback to restore CFN's auto-rollback.
NO_ROLLBACK=true
for arg in "$@"; do
  case "$arg" in
    --recreate) RECREATE=true ;;
    --skip-package) SKIP_PACKAGE=true ;;
    --agent-only) AGENT_ONLY=true ;;
    --voice-only) VOICE_ONLY=true ;;
    --no-rollback) NO_ROLLBACK=true ;;
    --rollback) NO_ROLLBACK=false ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

# ── Paths ────────────────────────────────────────────────────────────────────
# This script lives in infrastructure/scripts/, so repo root is two levels up.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PKG_SCRIPT="$REPO_ROOT/infrastructure/scripts/package_and_upload.sh"
VOICE_DIR="$REPO_ROOT/app/voice"
TEMPLATE_URL="https://${BUCKET}.s3.${REGION}.amazonaws.com/templates/main-stack.yaml"
ENV_NAME="${ENV_NAME:-agentic-analytics}"   # CFN EnvironmentName parameter (resource name prefix)

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[warn] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[error] %s\033[0m\n' "$*" >&2; exit 1; }

command -v aws >/dev/null || die "aws CLI not found"
command -v jq  >/dev/null || die "jq not found"
[ -f "$PKG_SCRIPT" ] || die "packaging script missing: $PKG_SCRIPT"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
log "Deploying to account ${ACCOUNT_ID}, region ${REGION}, bucket ${BUCKET}, stack ${STACK}"

# ── Fast path: agent code only (skip CloudFormation) ─────────────────────────
if [ "$AGENT_ONLY" = true ]; then
  AGENT_SRC="$REPO_ROOT/app/agentcore_strands"
  [ -d "$AGENT_SRC" ] || die "Agent source not found: $AGENT_SRC"

  log "[agent-only] Zipping agent code"
  AGENT_ZIP="$(mktemp).zip"
  (cd "$AGENT_SRC" && zip -r "$AGENT_ZIP" \
      unicorn_rental_agent.py \
      unicorn_rental_analytics.sop.md \
      requirements.txt \
      config.env.sample \
      -x "*.pyc" "*__pycache__*" > /dev/null)

  log "[agent-only] Uploading agent_code.zip -> s3://${BUCKET}/agent/agent_code.zip"
  aws s3 cp "$AGENT_ZIP" "s3://${BUCKET}/agent/agent_code.zip" --region "$REGION"
  rm -f "$AGENT_ZIP"

  # The runtime loads its SOP from s3://${BUCKET}/sops/ (SOP_S3_BUCKET is set, and
  # load_system_prompt() prefers S3 over the bundled copy). The agent_code.zip carries
  # a local SOP, but the running container never reads it — so a SOP edit is invisible
  # unless we ALSO push it to S3 here. (Full deploys do this in package_and_upload.sh.)
  log "[agent-only] Uploading SOP -> s3://${BUCKET}/sops/unicorn_rental_analytics.sop.md"
  aws s3 cp "$AGENT_SRC/unicorn_rental_analytics.sop.md" \
    "s3://${BUCKET}/sops/unicorn_rental_analytics.sop.md" --region "$REGION"

  log "[agent-only] Triggering CodeBuild project ${ENV_NAME}-agent-build"
  BUILD_ID="$(aws codebuild start-build \
    --project-name "${ENV_NAME}-agent-build" \
    --region "$REGION" \
    --query 'build.id' --output text)"
  log "[agent-only] Build ${BUILD_ID} started — waiting (~3 min)…"
  while true; do
    sleep 15
    BUILD_STATUS="$(aws codebuild batch-get-builds --ids "$BUILD_ID" --region "$REGION" \
      --query 'builds[0].buildStatus' --output text)"
    printf '  build status: %s\n' "$BUILD_STATUS"
    case "$BUILD_STATUS" in
      SUCCEEDED) break ;;
      FAILED|FAULT|STOPPED|TIMED_OUT) die "CodeBuild ${BUILD_ID} ended with status ${BUILD_STATUS}" ;;
    esac
  done
  log "[agent-only] Docker image pushed to ECR :latest"

  # Read current runtime config so we can pass the required fields back unchanged.
  RUNTIME_ID="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeId'].OutputValue" --output text)"
  [ -n "$RUNTIME_ID" ] || die "AgentRuntimeId not found in stack ${STACK} — has the stack been deployed?"

  RT_JSON="$(aws bedrock-agentcore-control get-agent-runtime \
    --agent-runtime-id "$RUNTIME_ID" --region "$REGION")"
  ROLE_ARN="$(echo "$RT_JSON" | jq -r '.roleArn')"
  CONTAINER_URI="$(echo "$RT_JSON" | jq -r '.agentRuntimeArtifact.containerConfiguration.containerUri')"
  NETWORK_MODE="$(echo "$RT_JSON" | jq -c '.networkConfiguration')"
  ENV_VARS="$(echo "$RT_JSON" | jq -c '.environmentVariables // {}')"
  # CRITICAL: update-agent-runtime REPLACES the runtime config — any field we don't
  # pass is reset to its default. The JWT inbound authorizer + the Authorization
  # request-header allowlist are NOT defaults, so we MUST read them back and re-send
  # them, or the runtime silently reverts to IAM/SigV4 auth and every Bearer request
  # 403s ("Authorization method mismatch"). (Learned the hard way — see the
  # voice-turn / guardrail debugging session.)
  AUTHZ_CFG="$(echo "$RT_JSON" | jq -c '.authorizerConfiguration // empty')"
  HDR_CFG="$(echo "$RT_JSON" | jq -c '.requestHeaderConfiguration // empty')"
  EXTRA_ARGS=()
  if [ -n "$AUTHZ_CFG" ]; then EXTRA_ARGS+=(--authorizer-configuration "$AUTHZ_CFG"); fi
  if [ -n "$HDR_CFG" ]; then EXTRA_ARGS+=(--request-header-configuration "$HDR_CFG"); fi

  log "[agent-only] Updating AgentCore Runtime ${RUNTIME_ID} (forces re-pull of :latest)"
  log "[agent-only]   preserving authorizer=$([ -n "$AUTHZ_CFG" ] && echo JWT || echo default), header-allowlist=$([ -n "$HDR_CFG" ] && echo yes || echo no)"
  UPD_JSON="$(aws bedrock-agentcore-control update-agent-runtime \
    --agent-runtime-id "$RUNTIME_ID" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${CONTAINER_URI}\"}}" \
    --role-arn "$ROLE_ARN" \
    --network-configuration "$NETWORK_MODE" \
    --environment-variables "$ENV_VARS" \
    ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
    --region "$REGION")"
  NEW_VERSION="$(echo "$UPD_JSON" | jq -r '.agentRuntimeVersion')"
  log "[agent-only] New runtime version: ${NEW_VERSION} — waiting for READY…"
  while true; do
    sleep 15
    RT_STATUS="$(aws bedrock-agentcore-control get-agent-runtime \
      --agent-runtime-id "$RUNTIME_ID" \
      --agent-runtime-version "$NEW_VERSION" \
      --region "$REGION" \
      --query 'status' --output text)"
    printf '  runtime status: %s\n' "$RT_STATUS"
    case "$RT_STATUS" in
      READY) break ;;
      FAILED) die "AgentCore Runtime version ${NEW_VERSION} failed to become READY" ;;
    esac
  done

  log "[agent-only] Updating endpoint to version ${NEW_VERSION}"
  aws bedrock-agentcore-control update-agent-runtime-endpoint \
    --agent-runtime-id "$RUNTIME_ID" \
    --endpoint-name "agentic_analytics_endpoint" \
    --agent-runtime-version "$NEW_VERSION" \
    --region "$REGION" > /dev/null

  log "Agent update complete ✅  (runtime ${RUNTIME_ID}, version ${NEW_VERSION})"
  exit 0
fi

# ── Fast path: voice agent code only (skip CloudFormation) ───────────────────
# Mirrors --agent-only for the VOICE AgentCore Runtime (WebRTC + KVS TURN). Re-zips
# app/voice, rebuilds the voice image, and update-agent-runtime on the voice runtime.
# CRITICAL: the voice runtime is VPC-mode + JWT — update-agent-runtime REPLACES config,
# so we re-send authorizerConfiguration + requestHeaderConfiguration + networkConfiguration
# (the VPC subnets/SGs) or it reverts to IAM/PUBLIC and breaks. (We never trust a
# mutable :latest tag to re-pull on its own — the new runtime VERSION forces a pull.)
if [ "$VOICE_ONLY" = true ]; then
  [ -f "$VOICE_DIR/bot.py" ] || die "Voice source not found: $VOICE_DIR"

  # The voice CodeBuild reads its source from the (hash-versioned) key wired at
  # stack-create; upload to THAT exact key so the rebuild picks up our new code.
  VOICE_SRC_LOC="$(aws codebuild batch-get-projects --names "${ENV_NAME}-voice-agentcore-build" \
    --region "$REGION" --query 'projects[0].source.location' --output text 2>/dev/null)"
  [ -n "$VOICE_SRC_LOC" ] && [ "$VOICE_SRC_LOC" != "None" ] || die "voice-build project source not found — is voice deployed (VoiceMode=agentcore)?"
  VOICE_KEY="${VOICE_SRC_LOC#*/}"   # strip "bucket/" → the S3 key

  log "[voice-only] Zipping voice code"
  VOICE_ZIP="$(mktemp).zip"
  VOICE_TMP="$(mktemp -d)"
  cp "$VOICE_DIR/bot.py" "$VOICE_DIR/analytics_processor.py" "$VOICE_DIR/auth.py" \
     "$VOICE_DIR/pyproject.toml" "$VOICE_DIR/uv.lock" "$VOICE_TMP/"
  (cd "$VOICE_TMP" && zip -r "$VOICE_ZIP" . > /dev/null)
  rm -rf "$VOICE_TMP"

  log "[voice-only] Uploading voice_agent_code -> s3://${BUCKET}/${VOICE_KEY}"
  aws s3 cp "$VOICE_ZIP" "s3://${BUCKET}/${VOICE_KEY}" --region "$REGION"
  rm -f "$VOICE_ZIP"

  log "[voice-only] Triggering CodeBuild project ${ENV_NAME}-voice-agentcore-build"
  BUILD_ID="$(aws codebuild start-build \
    --project-name "${ENV_NAME}-voice-agentcore-build" \
    --region "$REGION" \
    --query 'build.id' --output text)"
  log "[voice-only] Build ${BUILD_ID} started — waiting (~5-10 min, arm64 aiortc/numba)…"
  while true; do
    sleep 15
    BUILD_STATUS="$(aws codebuild batch-get-builds --ids "$BUILD_ID" --region "$REGION" \
      --query 'builds[0].buildStatus' --output text)"
    printf '  build status: %s\n' "$BUILD_STATUS"
    case "$BUILD_STATUS" in
      SUCCEEDED) break ;;
      FAILED|FAULT|STOPPED|TIMED_OUT) die "CodeBuild ${BUILD_ID} ended with status ${BUILD_STATUS}" ;;
    esac
  done
  log "[voice-only] Docker image pushed to ECR :latest"

  RUNTIME_ID="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='VoiceAgentRuntimeId'].OutputValue" --output text)"
  # The voice runtime id lives in the nested stack output; fall back to listing it.
  if [ -z "$RUNTIME_ID" ] || [ "$RUNTIME_ID" = "None" ]; then
    RUNTIME_ID="$(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
      --query "agentRuntimes[?agentRuntimeName=='agentic_analytics_voice'].agentRuntimeId | [0]" --output text 2>/dev/null)"
  fi
  [ -n "$RUNTIME_ID" ] && [ "$RUNTIME_ID" != "None" ] || die "voice runtime id not found — is voice deployed?"

  RT_JSON="$(aws bedrock-agentcore-control get-agent-runtime \
    --agent-runtime-id "$RUNTIME_ID" --region "$REGION")"
  ROLE_ARN="$(echo "$RT_JSON" | jq -r '.roleArn')"
  CONTAINER_URI="$(echo "$RT_JSON" | jq -r '.agentRuntimeArtifact.containerConfiguration.containerUri')"
  NETWORK_MODE="$(echo "$RT_JSON" | jq -c '.networkConfiguration')"   # VPC mode — must preserve
  ENV_VARS="$(echo "$RT_JSON" | jq -c '.environmentVariables // {}')"
  AUTHZ_CFG="$(echo "$RT_JSON" | jq -c '.authorizerConfiguration // empty')"
  HDR_CFG="$(echo "$RT_JSON" | jq -c '.requestHeaderConfiguration // empty')"
  EXTRA_ARGS=()
  if [ -n "$AUTHZ_CFG" ]; then EXTRA_ARGS+=(--authorizer-configuration "$AUTHZ_CFG"); fi
  if [ -n "$HDR_CFG" ]; then EXTRA_ARGS+=(--request-header-configuration "$HDR_CFG"); fi

  log "[voice-only] Updating voice runtime ${RUNTIME_ID} (forces re-pull of :latest)"
  log "[voice-only]   preserving authorizer=$([ -n "$AUTHZ_CFG" ] && echo JWT || echo default), header-allowlist=$([ -n "$HDR_CFG" ] && echo yes || echo no), network=$(echo "$NETWORK_MODE" | jq -r '.networkMode')"
  UPD_JSON="$(aws bedrock-agentcore-control update-agent-runtime \
    --agent-runtime-id "$RUNTIME_ID" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${CONTAINER_URI}\"}}" \
    --role-arn "$ROLE_ARN" \
    --network-configuration "$NETWORK_MODE" \
    --environment-variables "$ENV_VARS" \
    ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
    --region "$REGION")"
  NEW_VERSION="$(echo "$UPD_JSON" | jq -r '.agentRuntimeVersion')"
  log "[voice-only] New runtime version: ${NEW_VERSION} — waiting for READY…"
  while true; do
    sleep 15
    RT_STATUS="$(aws bedrock-agentcore-control get-agent-runtime \
      --agent-runtime-id "$RUNTIME_ID" \
      --agent-runtime-version "$NEW_VERSION" \
      --region "$REGION" \
      --query 'status' --output text)"
    printf '  runtime status: %s\n' "$RT_STATUS"
    case "$RT_STATUS" in
      READY) break ;;
      FAILED) die "Voice runtime version ${NEW_VERSION} failed to become READY" ;;
    esac
  done

  log "[voice-only] Updating endpoint to version ${NEW_VERSION}"
  aws bedrock-agentcore-control update-agent-runtime-endpoint \
    --agent-runtime-id "$RUNTIME_ID" \
    --endpoint-name "agentic_analytics_voice_endpoint" \
    --agent-runtime-version "$NEW_VERSION" \
    --region "$REGION" > /dev/null

  log "Voice update complete ✅  (runtime ${RUNTIME_ID}, version ${NEW_VERSION})"
  exit 0
fi

# ── 1. Package & upload artifacts (templates, Lambdas, agent code, UI, data) ──
PKG_LOG="$(mktemp)"
if [ "$SKIP_PACKAGE" = true ]; then
  warn "Skipping packaging (--skip-package). Reusing artifacts already in s3://${BUCKET}."
  warn "Lambda keys will be discovered from S3; if that fails, re-run without --skip-package."
else
  log "Ensuring artifacts bucket exists"
  aws s3 mb "s3://${BUCKET}" --region "$REGION" 2>/dev/null || true

  log "Packaging & uploading artifacts (this builds the UI + Lambdas — a few minutes)"
  AWS_REGION="$REGION" bash "$PKG_SCRIPT" "$BUCKET" | tee "$PKG_LOG"
fi

# ── 2. Discover the Lambda/artifact S3 keys ───────────────────────────────────
# macOS ships bash 3.2 (no associative arrays), so keys are stored as an indexed
# array of "Name=value" strings and looked up with key_val().
KEY_PAIRS=()
key_val() {  # key_val <Name> -> prints value or empty
  local want="$1" pair
  for pair in "${KEY_PAIRS[@]}"; do
    [ "${pair%%=*}" = "$want" ] && { echo "${pair#*=}"; return; }
  done
}

if [ "$SKIP_PACKAGE" = false ]; then
  # The packaging script prints a "Lambda S3 Keys:" block of "  NameKey=value"
  # lines AND a later "Deploy command:" echo containing "ParameterKey=...". Match
  # only the former: leading spaces, a bare <Name>Key token, '=', no comma in the
  # value (the ParameterKey lines are "ParameterKey=X,ParameterValue=Y").
  while IFS='=' read -r k v; do
    k="$(echo "$k" | xargs)"; v="$(echo "$v" | xargs)"
    [ "$k" = "ParameterKey" ] && continue
    [ -n "$k" ] && [ -n "$v" ] && KEY_PAIRS+=("${k}=${v}")
  done < <(grep -E '^[[:space:]]+[A-Za-z0-9]+(Key|S3Key)=[^,]+$' "$PKG_LOG")
else
  # Reconstruct from S3 (hash-versioned keys: pick newest per prefix).
  latest() { aws s3 ls "s3://${BUCKET}/$1" --region "$REGION" | sort | tail -1 | awk '{print $4}'; }
  KEY_PAIRS+=("DatabaseInitLambdaKey=lambdas/$(latest lambdas/database_init-)")
  KEY_PAIRS+=("GlueCrawlerLambdaKey=lambdas/$(latest lambdas/glue_crawler_trigger-)")
  KEY_PAIRS+=("BedrockKBLambdaKey=lambdas/$(latest lambdas/bedrock_kb_ingestion-)")
  KEY_PAIRS+=("ObservabilityLambdaKey=lambdas/$(latest lambdas/observability_setup-)")
  KEY_PAIRS+=("AmplifyLambdaKey=lambdas/$(latest lambdas/amplify_hosting-)")
  KEY_PAIRS+=("InterceptorLambdaKey=lambdas/$(latest lambdas/gateway_interceptor-)")
  KEY_PAIRS+=("DataFoundationLambdaKey=lambdas/$(latest lambdas/datafoundation-)")
  KEY_PAIRS+=("ApiIntegLambdaKey=lambdas/$(latest lambdas/api_integration_toolset-)")
  KEY_PAIRS+=("CustomSqlLambdaKey=lambdas/$(latest lambdas/custom_sql_toolset-)")
  KEY_PAIRS+=("SemanticLayerLambdaKey=lambdas/$(latest lambdas/semantic_layer_toolset-)")
  KEY_PAIRS+=("UIBuildKey=ui/$(latest ui/build-)")
  KEY_PAIRS+=("AgentCodeS3Key=agent/agent_code.zip")
  [ "$ENABLE_VOICE" = "true" ] && KEY_PAIRS+=("VoiceAgentCodeS3Key=voice/$(latest voice/voice_agent_code-)")
fi

REQUIRED_KEYS=(DatabaseInitLambdaKey GlueCrawlerLambdaKey BedrockKBLambdaKey
  ObservabilityLambdaKey AmplifyLambdaKey DataFoundationLambdaKey InterceptorLambdaKey
  ApiIntegLambdaKey CustomSqlLambdaKey SemanticLayerLambdaKey UIBuildKey AgentCodeS3Key)
for k in "${REQUIRED_KEYS[@]}"; do
  [ -n "$(key_val "$k")" ] || die "Could not resolve artifact key: $k"
done
log "Resolved ${#REQUIRED_KEYS[@]} artifact keys"

# (The single unified SOP — unicorn_rental_analytics.sop.md — is uploaded to
# s3://$BUCKET/sops/ by package_and_upload.sh; no separate voice-SOP step needed.)

# ── 4. Deploy the CloudFormation stack (create / recover / update) ────────────
stack_status() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
    --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo "DOES_NOT_EXIST"
}

# Resolve voice keys (env first, then app/voice/.env) when voice is enabled.
# agentcore mode (WebRTC + KVS) needs only Deepgram — no Daily key.
voice_env() { [ -f "$VOICE_DIR/.env" ] && grep -E "^$1=" "$VOICE_DIR/.env" | head -1 | cut -d= -f2- || true; }
if [ "$ENABLE_VOICE" = "true" ]; then
  DEEPGRAM_API_KEY="${DEEPGRAM_API_KEY:-$(voice_env DEEPGRAM_API_KEY)}"
  DEEPGRAM_VOICE_ID="${DEEPGRAM_VOICE_ID:-$(voice_env DEEPGRAM_VOICE_ID)}"
  DEEPGRAM_VOICE_ID="${DEEPGRAM_VOICE_ID:-aura-2-apollo-en}"
  [ -n "$DEEPGRAM_API_KEY" ] || die "ENABLE_VOICE=true but DEEPGRAM_API_KEY is unset (and not in app/voice/.env)"
fi

build_params() {
  echo "ParameterKey=ArtifactsBucket,ParameterValue=${BUCKET}"
  echo "ParameterKey=DeployMode,ParameterValue=demo"
  for k in "${REQUIRED_KEYS[@]}"; do
    echo "ParameterKey=${k},ParameterValue=$(key_val "$k")"
  done
  if [ "$ENABLE_VOICE" = "true" ]; then
    echo "ParameterKey=VoiceAgentCodeS3Key,ParameterValue=$(key_val VoiceAgentCodeS3Key)"
    echo "ParameterKey=EnableVoice,ParameterValue=true"
    echo "ParameterKey=VoiceMode,ParameterValue=${VOICE_MODE}"
    echo "ParameterKey=DeepgramApiKey,ParameterValue=${DEEPGRAM_API_KEY}"
    echo "ParameterKey=DeepgramVoiceId,ParameterValue=${DEEPGRAM_VOICE_ID}"
  fi
}

ST="$(stack_status)"
log "Current stack status: $ST"

# Decide whether to delete-first or fix-forward.
# CRITICAL: match EXACT states, not substrings. A CREATE rollback (ROLLBACK_COMPLETE /
# ROLLBACK_FAILED) is an unrecoverable failed *create* → delete. But an UPDATE rollback
# (UPDATE_ROLLBACK_COMPLETE) is a HEALTHY, updatable state → must NOT be deleted. A
# substring regex like =~ ROLLBACK_COMPLETE wrongly matches UPDATE_ROLLBACK_COMPLETE and
# would destroy a live stack — so we compare exact values.
#  - CREATE_FAILED + disable-rollback: fix forward via update-stack (no teardown).
#  - --recreate always forces a clean delete.
DELETE_FIRST=false
case "$ST" in
  ROLLBACK_COMPLETE|ROLLBACK_FAILED|DELETE_FAILED)
    DELETE_FIRST=true ;;  # failed CREATE (or stuck delete) — unrecoverable
  CREATE_FAILED)
    if [ "$NO_ROLLBACK" != true ]; then
      DELETE_FIRST=true   # auto-rollback create failed → must recreate
    else
      log "Stack is CREATE_FAILED (disable-rollback) — fixing forward via update-stack (no teardown)."
    fi ;;
esac
if [ "$RECREATE" = true ]; then
  DELETE_FIRST=true
fi
# UPDATE_COMPLETE, UPDATE_ROLLBACK_COMPLETE, UPDATE_FAILED, etc. → fall through to the
# update path (NEVER auto-delete a stack that has a healthy last-known-good state).
if [ "$DELETE_FIRST" = true ] && [ "$ST" != "DOES_NOT_EXIST" ]; then
  warn "Deleting stack in state ${ST} before (re)create — this destroys its resources."
  aws cloudformation delete-stack --region "$REGION" --stack-name "$STACK"
  aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$STACK"
  ST="DOES_NOT_EXIST"
fi

CAPS="CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND"
# Use --disable-rollback (NOT --on-failure DO_NOTHING): both leave a failed create
# as CREATE_FAILED with resources intact, BUT only --disable-rollback lets you then
# fix forward with `update-stack` (AWS rejects update on an on-failure=DO_NOTHING
# stack: "Please use disable-rollback option instead during stack creation").
CREATE_RB=()
[ "$NO_ROLLBACK" = true ] && CREATE_RB=(--disable-rollback)
if [ "$ST" = "DOES_NOT_EXIST" ]; then
  log "Creating stack ${STACK} (disable-rollback=${NO_ROLLBACK}; ~25-35 min: Aurora, VPC/NAT, Glue, KB, AgentCore build, Cognito, Amplify)"
  aws cloudformation create-stack --region "$REGION" --stack-name "$STACK" \
    --template-url "$TEMPLATE_URL" \
    --parameters $(build_params) \
    ${CREATE_RB[@]+"${CREATE_RB[@]}"} \
    --capabilities $CAPS >/dev/null
  log "Waiting for CREATE_COMPLETE…"
  aws cloudformation wait stack-create-complete --region "$REGION" --stack-name "$STACK" \
    || die "Stack create failed — resources left in place (disable-rollback). Fix the cause, repackage, then re-run: the script will update-stack to fix forward. Inspect: aws cloudformation describe-stack-events --stack-name $STACK --region $REGION"
else
  log "Updating existing stack ${STACK}"
  # NOTE: --disable-rollback REJECTS replacement-type updates ("Replacement type
  # updates not supported on stack with disable-rollback"). Pass --rollback when an
  # update replaces a resource (e.g. a new TaskDefinition revision).
  # bash 3.2 + set -u: expanding an empty array as "${arr[@]}" errors, so guard it.
  DISABLE_RB=()
  [ "$NO_ROLLBACK" = true ] && DISABLE_RB=(--disable-rollback)
  if aws cloudformation update-stack --region "$REGION" --stack-name "$STACK" \
       --template-url "$TEMPLATE_URL" \
       --parameters $(build_params) \
       ${DISABLE_RB[@]+"${DISABLE_RB[@]}"} \
       --capabilities $CAPS >/dev/null 2>/tmp/upd_err; then
    log "Waiting for UPDATE_COMPLETE…"
    aws cloudformation wait stack-update-complete --region "$REGION" --stack-name "$STACK" \
      || die "Stack update failed — inspect stack events."
  else
    if grep -q "No updates are to be performed" /tmp/upd_err; then
      log "No stack changes needed."
    else
      cat /tmp/upd_err >&2; die "update-stack failed."
    fi
  fi
fi
log "Stack is ready: $(stack_status)"

# ── 5. Read stack outputs ──────────────────────────────────────────────────--
log "Reading stack outputs"
OUT_JSON="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Outputs" --output json)"
get_out() { echo "$OUT_JSON" | jq -r --arg k "$1" '.[] | select(.OutputKey==$k) | .OutputValue'; }

RUNTIME_ID="$(get_out AgentRuntimeId)"
COGNITO_CLIENT_ID="$(get_out CognitoClientId)"
COGNITO_USER_POOL_ID="$(get_out CognitoUserPoolId)"
AMPLIFY_URL="$(get_out AmplifyAppUrl)"
[ -n "$RUNTIME_ID" ] || die "AgentRuntimeId output missing"
AGENT_ARN="arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:runtime/${RUNTIME_ID}"

# Demo user creds live in SSM (seeded by the Cognito stack). Pick by role.
USERS_JSON="$(aws ssm get-parameter --region "$REGION" --name "/agentic-analytics/demo/users" \
  --with-decryption --query "Parameter.Value" --output text)"
DEMO_USERNAME="$(echo "$USERS_JSON" | jq -r --arg r "$DEMO_ROLE" 'map(select(.role==$r)) | .[0].email // empty')"
DEMO_PASSWORD="$(echo "$USERS_JSON" | jq -r --arg r "$DEMO_ROLE" 'map(select(.role==$r)) | .[0].password // empty')"
[ -n "$DEMO_USERNAME" ] || die "No demo user with role ${DEMO_ROLE} found in SSM"

# ── 6. Write app/voice/.env (preserving existing secret keys) ────────────────-
ENV_FILE="$VOICE_DIR/.env"
get_existing() { [ -f "$ENV_FILE" ] && grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- || true; }
DAILY_API_KEY="$(get_existing DAILY_API_KEY)"
DEEPGRAM_API_KEY="$(get_existing DEEPGRAM_API_KEY)"
DEEPGRAM_VOICE_ID="$(get_existing DEEPGRAM_VOICE_ID)"; DEEPGRAM_VOICE_ID="${DEEPGRAM_VOICE_ID:-aura-2-helena-en}"
USE_FLUX="$(get_existing USE_FLUX)"; USE_FLUX="${USE_FLUX:-false}"
USE_SAGEMAKER="$(get_existing USE_SAGEMAKER)"; USE_SAGEMAKER="${USE_SAGEMAKER:-false}"

log "Writing ${ENV_FILE} (preserving Daily/Deepgram keys if already set)"
cat > "$ENV_FILE" <<EOF
# Local secrets — gitignored. Generated by infrastructure/scripts/deploy_backend.sh.
# Backend: ${STACK} in account ${ACCOUNT_ID}, region ${REGION}.

# ── Transport ──────────────────────────────────────────────────────────────────
DAILY_API_KEY=${DAILY_API_KEY}

# ── Deepgram ───────────────────────────────────────────────────────────────────
DEEPGRAM_API_KEY=${DEEPGRAM_API_KEY}
DEEPGRAM_VOICE_ID=${DEEPGRAM_VOICE_ID}
USE_FLUX=${USE_FLUX}
USE_SAGEMAKER=${USE_SAGEMAKER}

# ── AWS ──────────────────────────────────────────────────────────────────────--
# Uses your ambient AWS creds (same account the backend is deployed in).
AWS_REGION=${REGION}

# ── AgentCore (deployed Strands agent) ─────────────────────────────────────────
AWS_AGENT_ARN=${AGENT_ARN}

# ── Cognito ──────────────────────────────────────────────────────────────────
COGNITO_CLIENT_ID=${COGNITO_CLIENT_ID}
COGNITO_USER_POOL_ID=${COGNITO_USER_POOL_ID}

# ── Laptop-dev demo identity (LOCAL ONLY) ──────────────────────────────────────
# Hosted modes (fargate/pipecat-cloud) forward the SIGNED-IN user's own token and
# never use these. For laptop dev with no browser sign-in, ALLOW_DEMO_FALLBACK=true
# lets auth.py mint a token via Cognito ROPC for the demo user (role=${DEMO_ROLE}).
ALLOW_DEMO_FALLBACK=true
DEMO_USERNAME=${DEMO_USERNAME}
DEMO_PASSWORD=${DEMO_PASSWORD}
EOF

if [ -z "$DAILY_API_KEY" ] || [ -z "$DEEPGRAM_API_KEY" ]; then
  warn "DAILY_API_KEY / DEEPGRAM_API_KEY are blank in ${ENV_FILE} — fill them before running the bot."
fi

# ── Done ─────────────────────────────────────────────────────────────────────-
log "Backend ready ✅"
cat <<EOF

  Agent ARN     : ${AGENT_ARN}
  Cognito pool  : ${COGNITO_USER_POOL_ID}  (client ${COGNITO_CLIENT_ID})
  Demo user     : ${DEMO_USERNAME}  (${DEMO_ROLE}, laptop-dev fallback)
  Amplify UI    : ${AMPLIFY_URL}   (text chat + voice)

  Run the voice bot (option 1 — Pipecat Playground):
    cd app/voice && uv sync && uv run bot.py --transport daily
    # then open the printed http://localhost:7860 URL and talk

  Tear down the backend when done (stops Aurora + NAT billing):
    aws cloudformation delete-stack --stack-name ${STACK} --region ${REGION}
EOF
rm -f "$PKG_LOG" /tmp/upd_err 2>/dev/null || true
