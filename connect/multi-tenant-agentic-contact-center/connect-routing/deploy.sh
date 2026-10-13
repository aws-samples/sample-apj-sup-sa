#!/bin/bash
# Deploy the OPT-IN Connect routing module (case-owner reply routing + after-hours
# case/task backlog), then wire merchant chats to its routed flow.
#
# Additive: it creates new hours, queues, routing profiles, Cases fields + an OOH
# template, two Lambdas and two NEW flows. The existing inbound/case chat flows,
# support queue and routing profile are not modified. The only change to an
# existing stack is the ChatApiFn flow pointer, made by redeploying
# AnyCompanyPayConnectStack with -c routingFlowArn (step 2). Undo it any time with
# UNWIRE=1 (reverts to the original flows; the routing stack stays deployed).
#
# Prereqs (same account + region): AnyCompanyPayConnectStack deployed. Optional:
# AnyCompanyPayLexStack (agentic self-service is preserved in the routed flow and
# on the connect stack when it exists).
#
# Env overrides:
#   R / AWS_REGION      target region (default: configured region)
#   ENV_NAME            env discriminator (default: empty)
#   DEMO_MODE=1         force the after-hours path + schedule tasks ~2 min out
#   ALERT_EMAIL=...     subscribe an address to OOH scheduling-failure alerts
#   WIRE=0              deploy the routing stack only; don't switch ChatApiFn
#   UNWIRE=1            switch ChatApiFn back to the original flows and exit
#   EXTRA_CTX="-c k=v"  extra CDK context (csTimeZone, csOpen, csClose, csDays,
#                       ownerPresenceCheck, ownerExpiryChatSeconds, tierDefaults, ...)
set -euo pipefail
cd "$(dirname "$0")"

R="${R:-${AWS_REGION:-${CDK_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-west-2)}}}"
ENV_NAME="${ENV_NAME:-}"
export CDK_DEFAULT_REGION="$R" AWS_REGION="$R"
export CDK_DEFAULT_ACCOUNT="${CDK_DEFAULT_ACCOUNT:-$(aws sts get-caller-identity --query Account --output text)}"
SFX="${ENV_NAME:+-${ENV_NAME}}"
CONNECT_STACK="AnyCompanyPayConnectStack${SFX}"
ROUTING_STACK="AnyCompanyPayConnectRoutingStack${SFX}"

get_out() {
  aws cloudformation describe-stacks --region "$R" --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" --output text 2>/dev/null || true
}
require() {
  if [[ -z "$2" || "$2" == "None" ]]; then
    echo "ERROR: could not discover '$1'. Is $CONNECT_STACK deployed in $R?" >&2; exit 1
  fi
}

echo "==> Region: $R   Env: ${ENV_NAME:-<none>}   Account: $CDK_DEFAULT_ACCOUNT"
INSTANCE_ARN=$(get_out "$CONNECT_STACK" ConnectInstanceArn); require ConnectInstanceArn "$INSTANCE_ARN"
CASES_DOMAIN_ID=$(get_out "$CONNECT_STACK" CasesDomainId); require CasesDomainId "$CASES_DOMAIN_ID"
PROFILES_DOMAIN=$(get_out "$CONNECT_STACK" CustomerProfilesDomainName)
[[ -z "$PROFILES_DOMAIN" || "$PROFILES_DOMAIN" == "None" ]] && PROFILES_DOMAIN="anycompany-pay-customer-profile${SFX}"
PROFILES_KEY=$(aws customer-profiles get-domain --region "$R" --domain-name "$PROFILES_DOMAIN" \
  --query DefaultEncryptionKey --output text 2>/dev/null || true)
# Agentic self-service (optional): keep it on both the connect stack and the routed flow.
BOT_ALIAS_ARN=$(get_out AnyCompanyPayLexStack BotAliasArn)
ASSISTANT_ARN=$(get_out AnyCompanyPayLexStack AssistantArn)
AGENTIC_CTX=()
if [[ -n "$BOT_ALIAS_ARN" && "$BOT_ALIAS_ARN" != "None" && -n "$ASSISTANT_ARN" && "$ASSISTANT_ARN" != "None" ]]; then
  AGENTIC_CTX=(-c agenticBotAliasArn="$BOT_ALIAS_ARN" -c qicAssistantArn="$ASSISTANT_ARN")
  echo "    agentic self-service detected (Lex $BOT_ALIAS_ARN)"
fi

wire_connect_stack() { # $1 = routed flow ARN, or "" to unwire
  # Shared redeploy keeps every other opt-in module (agentic, screen share) as is.
  echo "==> Redeploying $CONNECT_STACK (ChatApiFn flow -> ${1:-original flows})"
  if [[ -n "$1" ]]; then ROUTING=on bash ../infra/deploy-connect-stack.sh; else ROUTING=off bash ../infra/deploy-connect-stack.sh; fi
}

if [[ "${UNWIRE:-0}" == "1" ]]; then
  wire_connect_stack ""
  echo "=== Merchant chats use the original flows again ($ROUTING_STACK left deployed). ==="
  exit 0
fi

npm install --silent
CTX=(-c envName="$ENV_NAME" -c connectInstanceArn="$INSTANCE_ARN" -c casesDomainId="$CASES_DOMAIN_ID"
     -c profilesDomainName="$PROFILES_DOMAIN" -c demoMode="$([[ "${DEMO_MODE:-0}" == "1" ]] && echo true || echo false)")
[[ -n "$PROFILES_KEY" && "$PROFILES_KEY" != "None" ]] && CTX+=(-c profilesKeyArn="$PROFILES_KEY")
[[ -n "${ALERT_EMAIL:-}" ]] && CTX+=(-c alertEmail="$ALERT_EMAIL")
echo "==> Deploying $ROUTING_STACK (demoMode=${DEMO_MODE:-0})"
# shellcheck disable=SC2086
npx cdk deploy "$ROUTING_STACK" --require-approval never --outputs-file cdk-outputs.json \
  "${CTX[@]}" "${AGENTIC_CTX[@]+"${AGENTIC_CTX[@]}"}" ${EXTRA_CTX:-}

ROUTED_FLOW_ARN=$(get_out "$ROUTING_STACK" RoutedChatFlowArn); require RoutedChatFlowArn "$ROUTED_FLOW_ARN"
if [[ "${WIRE:-1}" == "1" ]]; then
  wire_connect_stack "$ROUTED_FLOW_ARN"
fi

echo
echo "=== Done. Routed chat flow: $ROUTED_FLOW_ARN ==="
echo "Next: bash provision-tiers.sh   (merchant tier on Customer Profiles)"
echo "      bash provision-agents.sh  (demo agents on RP-Live / RP-OOH-Backlog)"
