#!/bin/bash
# Deploy the OPT-IN agent-to-agent (A2A) demo: Amazon Connect hands a chat to an
# external AI agent (a mock "Level3" partner) over the Connect A2A extension.
#
# Additive: a new stack (KMS key, API-key secret, mock partner WebSocket endpoint,
# a NEW Lex bot + chat flow), plus API-only Connect objects created by
# provision-a2a.sh (an AppIntegrations application, its instance association, a
# NEW security profile, a NEW prompt + AI agent). Existing flows, the self-service AI agent and the
# merchant chat wiring are not changed. Start a chat on the new flow to try it
# (see README).
#
# Prereqs (same account + region): AnyCompanyPayConnectStack and
# AnyCompanyPayLexStack (its Q in Connect assistant; this module brings its own
# Lex bot). The instance must be on the Connect Customer tier.
#
# Env overrides:
#   R / AWS_REGION   target region (default: configured region)
#   ENV_NAME         env discriminator (default: empty)
set -euo pipefail
cd "$(dirname "$0")"

R="${R:-${AWS_REGION:-${CDK_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-west-2)}}}"
ENV_NAME="${ENV_NAME:-}"
export CDK_DEFAULT_REGION="$R" AWS_REGION="$R"
export CDK_DEFAULT_ACCOUNT="${CDK_DEFAULT_ACCOUNT:-$(aws sts get-caller-identity --query Account --output text)}"
SFX="${ENV_NAME:+-${ENV_NAME}}"
CONNECT_STACK="AnyCompanyPayConnectStack${SFX}"
A2A_STACK="AnyCompanyPayConnectA2aStack${SFX}"

get_out() {
  aws cloudformation describe-stacks --region "$R" --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" --output text 2>/dev/null || true
}
require() {
  if [[ -z "$2" || "$2" == "None" ]]; then echo "ERROR: could not discover '$1' in $R." >&2; exit 1; fi
}

echo "==> Region: $R   Env: ${ENV_NAME:-<none>}   Account: $CDK_DEFAULT_ACCOUNT"
INSTANCE_ARN=$(get_out "$CONNECT_STACK" ConnectInstanceArn); require ConnectInstanceArn "$INSTANCE_ARN"
QUEUE_ARN=$(get_out "$CONNECT_STACK" SupportQueueArn); require SupportQueueArn "$QUEUE_ARN"
ASSISTANT_ARN=$(get_out AnyCompanyPayLexStack AssistantArn); require "AssistantArn (AnyCompanyPayLexStack)" "$ASSISTANT_ARN"

npm install --silent
CTX=(-c envName="$ENV_NAME" -c connectInstanceArn="$INSTANCE_ARN" -c supportQueueArn="$QUEUE_ARN"
     -c qicAssistantArn="$ASSISTANT_ARN")

# 1) key, secret and the mock partner endpoint (keeps an existing flow in place).
PREV_AGENT=$(get_out "$A2A_STACK" A2aAgentArn)
PREV_CTX=(); [[ -n "$PREV_AGENT" && "$PREV_AGENT" != "None" ]] && PREV_CTX=(-c a2aAgentArn="$PREV_AGENT")
echo "==> Deploying $A2A_STACK (partner endpoint + API key)"
npx cdk deploy "$A2A_STACK" --require-approval never "${CTX[@]}" ${PREV_CTX[@]+"${PREV_CTX[@]}"} >/dev/null 2>&1

# 2) register the partner with Connect + the handoff AI agent (API-only).
ENDPOINT_URL=$(get_out "$A2A_STACK" A2aEndpointUrl)
SECRET_ARN=$(get_out "$A2A_STACK" A2aApiKeySecretArn)
OUT=$(R="$R" ENV_NAME="$ENV_NAME" INSTANCE_ARN="$INSTANCE_ARN" ASSISTANT_ARN="$ASSISTANT_ARN" \
  ENDPOINT_URL="$ENDPOINT_URL" SECRET_ARN="$SECRET_ARN" bash provision-a2a.sh)
AGENT_ARN="${OUT##*A2A_AGENT_ARN=}"

# 3) the chat flow that uses that AI agent version.
echo "==> Deploying the A2A chat flow (AI agent $AGENT_ARN)"
npx cdk deploy "$A2A_STACK" --require-approval never "${CTX[@]}" -c a2aAgentArn="$AGENT_ARN" >/dev/null 2>&1

echo
echo "=== Done ==="
echo "Partner endpoint : $ENDPOINT_URL"
echo "A2A chat flow    : $(get_out "$A2A_STACK" A2aChatFlowArn)"
echo "AI agent version : $AGENT_ARN"
