#!/bin/bash
# Redeploy AnyCompanyPayConnectStack[-<env>] with EVERY opt-in module's context,
# so that redeploying for one module never silently reverts another.
#
# Opt-in modules and the context they contribute:
#   agentic self-service (AnyCompanyPayLexStack)        -c agenticBotAliasArn, qicAssistantArn
#   routing     (AnyCompanyPayConnectRoutingStack)      -c routingFlowArn, routingQueueArns
#   screenshare (AnyCompanyPayConnectScreenShareStack)  -c screenShareQueueArn
#
# Agentic is applied whenever its stack exists. Routing and screen share default
# to their CURRENT live state (read from the deployed resources), so a deploy for
# one module keeps the others exactly as they are. Change a module explicitly:
#   ROUTING=on|off       (off = merchant chats use the original flows again)
#   SCREENSHARE=on|off   (off = agent1/admin profile drops the screen-share queue)
# Also: R / AWS_REGION, ENV_NAME, DIFF_ONLY=1 (cdk diff instead of deploy).
set -euo pipefail
cd "$(dirname "$0")"

R="${R:-${AWS_REGION:-${CDK_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-west-2)}}}"
ENV_NAME="${ENV_NAME:-}"
SFX="${ENV_NAME:+-${ENV_NAME}}"
export CDK_DEFAULT_REGION="$R" AWS_REGION="$R"
export CDK_DEFAULT_ACCOUNT="${CDK_DEFAULT_ACCOUNT:-$(aws sts get-caller-identity --query Account --output text)}"
CONNECT_STACK="AnyCompanyPayConnectStack${SFX}"
ROUTING_STACK="AnyCompanyPayConnectRoutingStack${SFX}"
SCREENSHARE_STACK="AnyCompanyPayConnectScreenShareStack${SFX}"

get_out() {
  aws cloudformation describe-stacks --region "$R" --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" --output text 2>/dev/null || true
}
has() { [[ -n "$1" && "$1" != "None" ]]; }

CTX=(-c envName="$ENV_NAME")

# --- agentic self-service --------------------------------------------------
BOT_ALIAS_ARN=$(get_out AnyCompanyPayLexStack BotAliasArn)
ASSISTANT_ARN=$(get_out AnyCompanyPayLexStack AssistantArn)
if has "$BOT_ALIAS_ARN" && has "$ASSISTANT_ARN"; then
  CTX+=(-c agenticBotAliasArn="$BOT_ALIAS_ARN" -c qicAssistantArn="$ASSISTANT_ARN")
  echo "    agentic: on"
fi

# Current live state, used when ROUTING / SCREENSHARE are not set explicitly.
# (no `| [0]` here: the CLI applies --query per page, so filter after paginating)
CHAT_FN=$(aws lambda list-functions --region "$R" \
  --query "Functions[?starts_with(FunctionName, '${CONNECT_STACK}-ChatApiFn')].FunctionName" --output text 2>/dev/null \
  | tr '\t' '\n' | grep -v '^None$' | head -1 || true)
LIVE_FLOW=""
has "$CHAT_FN" && LIVE_FLOW=$(aws lambda get-function-configuration --region "$R" --function-name "$CHAT_FN" \
  --query "Environment.Variables.CONTACT_FLOW_ARN" --output text 2>/dev/null || true)
INSTANCE_ID=$(get_out "$CONNECT_STACK" ConnectInstanceId)

# --- routing ----------------------------------------------------------------
ROUTED_FLOW=$(get_out "$ROUTING_STACK" RoutedChatFlowArn)
ROUTING_STATE="${ROUTING:-}"
if [[ -z "$ROUTING_STATE" ]]; then
  ROUTING_STATE=off; has "$ROUTED_FLOW" && [[ "$LIVE_FLOW" == "$ROUTED_FLOW" ]] && ROUTING_STATE=on
fi
if [[ "$ROUTING_STATE" == "on" ]]; then
  has "$ROUTED_FLOW" || { echo "ERROR: ROUTING=on but $ROUTING_STACK is not deployed." >&2; exit 1; }
  Q="$(get_out "$ROUTING_STACK" VipChatQueueArn),$(get_out "$ROUTING_STACK" KeyAccountChatQueueArn),$(get_out "$ROUTING_STACK" SharedChatQueueArn)"
  CTX+=(-c routingFlowArn="$ROUTED_FLOW" -c routingQueueArns="$Q")
fi
echo "    routing: $ROUTING_STATE"

# --- screen share -------------------------------------------------------------
SS_QUEUE=$(get_out "$SCREENSHARE_STACK" ScreenShareQueueArn)
SS_STATE="${SCREENSHARE:-}"
if [[ -z "$SS_STATE" ]]; then
  SS_STATE=off
  if has "$SS_QUEUE" && has "$INSTANCE_ID"; then
    RP_ID=$(aws connect list-routing-profiles --region "$R" --instance-id "$INSTANCE_ID" \
      --query "RoutingProfileSummaryList[?Name=='anycompany-pay-chat${SFX}'].Id" --output text 2>/dev/null \
      | tr '\t' '\n' | grep -v '^None$' | head -1 || true)
    has "$RP_ID" && aws connect list-routing-profile-queues --region "$R" --instance-id "$INSTANCE_ID" \
      --routing-profile-id "$RP_ID" --query "RoutingProfileQueueConfigSummaryList[].QueueArn" --output text 2>/dev/null \
      | tr '\t' '\n' | grep -qx "$SS_QUEUE" && SS_STATE=on
  fi
fi
if [[ "$SS_STATE" == "on" ]]; then
  has "$SS_QUEUE" || { echo "ERROR: SCREENSHARE=on but $SCREENSHARE_STACK is not deployed." >&2; exit 1; }
  CTX+=(-c screenShareQueueArn="$SS_QUEUE")
fi
echo "    screenshare: $SS_STATE"

npm install --silent
if [[ "${DIFF_ONLY:-0}" == "1" ]]; then
  echo "==> Diff $CONNECT_STACK"
  npx cdk diff "$CONNECT_STACK" --exclusively "${CTX[@]}"
  exit 0
fi
echo "==> Deploying $CONNECT_STACK"
# --exclusively: only the connect stack, not its app-stack dependency.
npx cdk deploy "$CONNECT_STACK" --exclusively --require-approval never "${CTX[@]}"
