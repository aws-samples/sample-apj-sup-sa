#!/bin/bash
# Deploy the AnyCompanyPay agentic self-service Lex bot (Q in Connect) — AnyCompanyPayLexStack.
#
# Fully CloudFormation: an Amazon Lex V2 bot with the AMAZON.QInConnectIntent
# pointed at the instance's Q in Connect assistant, published + aliased, and
# associated to the Connect instance (LEX_BOT). Nothing is hardcoded: the region
# comes from the environment and the assistant ARN + instance ARN are DISCOVERED
# from the Connect instance / the connect stack outputs.
#
# Prereq: the Q in Connect AI domain must already exist on the instance (console
# step 1 — "Enable Amazon Q in Connect"), which creates the WISDOM_ASSISTANT
# integration this script reads.
#
# Env overrides:
#   R / AWS_REGION / CDK_DEFAULT_REGION   target region (default: configured region)
#   ENV_NAME                              environment discriminator (default: v2)
set -euo pipefail
cd "$(dirname "$0")"

R="${R:-${AWS_REGION:-${CDK_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-west-2)}}}"
if [[ -z "$R" ]]; then
  echo "ERROR: no region. Set AWS_REGION (or R, or CDK_DEFAULT_REGION), e.g. AWS_REGION=us-west-2 $0" >&2
  exit 1
fi
# Optional env discriminator. Empty by default → clean common stack names (no suffix).
ENV_NAME="${ENV_NAME:-}"
export CDK_DEFAULT_REGION="$R" AWS_REGION="$R"
export CDK_DEFAULT_ACCOUNT="${CDK_DEFAULT_ACCOUNT:-$(aws sts get-caller-identity --query Account --output text)}"
CONNECT_STACK="AnyCompanyPayConnectStack${ENV_NAME:+-${ENV_NAME}}"

echo "==> Region: $R   Env: ${ENV_NAME:-<none>}"

get_out() {
  aws cloudformation describe-stacks --region "$R" --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" --output text 2>/dev/null
}

echo "==> Discovering connect module outputs ($CONNECT_STACK)"
INSTANCE_ID=$(get_out "$CONNECT_STACK" ConnectInstanceId)
INSTANCE_ARN=$(get_out "$CONNECT_STACK" ConnectInstanceArn)
if [[ -z "$INSTANCE_ID" || "$INSTANCE_ID" == "None" ]]; then
  echo "ERROR: could not find $CONNECT_STACK ConnectInstanceId in $R. Deploy the connect stack first." >&2
  exit 1
fi

echo "==> Discovering the Q in Connect assistant (WISDOM_ASSISTANT integration)"
ASSISTANT_ARN=$(aws connect list-integration-associations --region "$R" --instance-id "$INSTANCE_ID" \
  --query "IntegrationAssociationSummaryList[?IntegrationType=='WISDOM_ASSISTANT'].IntegrationArn | [0]" \
  --output text 2>/dev/null)
if [[ -z "$ASSISTANT_ARN" || "$ASSISTANT_ARN" == "None" ]]; then
  echo "ERROR: no Q in Connect assistant (WISDOM_ASSISTANT) on instance $INSTANCE_ID." >&2
  echo "       Complete step 1 first: Connect console -> the instance -> enable Amazon Q in Connect / add the AI domain." >&2
  exit 1
fi

echo "    INSTANCE_ID=$INSTANCE_ID"
echo "    INSTANCE_ARN=$INSTANCE_ARN"
echo "    ASSISTANT_ARN=$ASSISTANT_ARN"

echo "==> Installing module dependencies"
npm install

echo "==> Deploying AnyCompanyPayLexStack (bot + locale + QInConnect intent + version + alias)"
npx cdk deploy AnyCompanyPayLexStack --require-approval never --outputs-file cdk-outputs-lex.json \
  -c envName="$ENV_NAME" \
  -c assistantArn="$ASSISTANT_ARN"

# Associate the bot ALIAS with the Connect instance. This is done here (a script
# step) and NOT in CloudFormation because AWS::Connect::IntegrationAssociation
# (LEX_BOT) is a no-op for Lex V2 - it never populates the instance bot store the
# flow's "Get customer input" block reads - and AssociateBot cannot be expressed
# as a stable idempotent CFN resource (it churns on any stack change and its
# resource-policy write silently no-ops under a scoped role). associate-bot also
# writes the Connect invoke resource-policy on the alias. Idempotent below.
BOT_ALIAS_ARN=$(python3 -c "import json;print(json.load(open('cdk-outputs-lex.json'))['AnyCompanyPayLexStack']['BotAliasArn'])" 2>/dev/null)
if [[ -z "$BOT_ALIAS_ARN" ]]; then
  echo "ERROR: could not read BotAliasArn from cdk-outputs-lex.json" >&2; exit 1
fi
echo "==> Associating bot alias with the instance (connect associate-bot)"
echo "    alias: $BOT_ALIAS_ARN"
if ASSOC_ERR=$(aws connect associate-bot --region "$R" --instance-id "$INSTANCE_ID" \
     --lex-v2-bot AliasArn="$BOT_ALIAS_ARN" 2>&1); then
  echo "    associated."
elif echo "$ASSOC_ERR" | grep -qi "DuplicateResource\|already"; then
  echo "    already associated (ok)."
else
  echo "    associate-bot failed:"; echo "$ASSOC_ERR"; exit 1
fi

echo
echo "=== Lex bot (Q in Connect) built via CloudFormation + associated to the instance. ==="

# ------------------------------------------------------------------------------
# Wire the inbound chat flow to the agentic self-service path. The connect stack
# already ships both flow variants (infra/lib/connect-stack.ts): a plain
# greet->queue flow, and an agentic flow (CreateWisdomSession -> stamp
# x-amz-lex:q-in-connect:session-arn -> ConnectParticipantWithLexBot -> queue
# fallback). It picks the agentic one when BOTH the Lex bot alias and the Q in
# Connect assistant ARNs are supplied as context. Both are now known (created
# above / discovered), so redeploy the connect stack with them to flip
# anycompany-pay-chat-inbound to the self-service flow. Idempotent: re-running with the
# same ARNs is a CFN no-op. This replaces the old manual "wire the chat flow in
# the console" step.
# ------------------------------------------------------------------------------
echo "==> Wiring the inbound chat flow to agentic self-service"
echo "    redeploying $CONNECT_STACK with the Lex alias + QIC assistant context"
echo "    agenticBotAliasArn=$BOT_ALIAS_ARN"
echo "    qicAssistantArn=$ASSISTANT_ARN"
# Shared redeploy: picks up the Lex alias + assistant from AnyCompanyPayLexStack
# (deployed above) and keeps the other opt-in modules (routing, screen share) in
# their current state.
R="$R" ENV_NAME="$ENV_NAME" bash ../infra/deploy-connect-stack.sh

echo
echo "=== Done. Inbound chat flow (anycompany-pay-chat-inbound) now routes to the agentic ==="
echo "=== self-service path (Q in Connect + Lex) before falling back to the queue. ==="
