#!/bin/bash
# OPTIONAL: create the Amazon Q in Connect "AI domain" (assistant) as CloudFormation
# (AnyCompanyPayQicDomainStack) instead of the console "AI agents -> Add domain" step.
#
# Use this only for a CLEAN-ROOM deploy on an instance that does NOT already have a
# Q in Connect domain. An instance can be associated with only ONE domain at a time,
# so this script refuses to run if a WISDOM_ASSISTANT association already exists.
#
# It does NOT reproduce the default system AI agents/prompts the console seeds; this
# solution creates its own orchestration agent via provision-ai-agent.sh, so that's fine.
#
# Env overrides:
#   R / AWS_REGION / CDK_DEFAULT_REGION   target region (default: configured region)
#   ENV_NAME                              environment discriminator (default: v2)
#   DOMAIN_NAME                           domain/assistant name (default: anycompany-pay-ai-domain-<env>)
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

INSTANCE_ARN=$(get_out "$CONNECT_STACK" ConnectInstanceArn)
INSTANCE_ID=$(get_out "$CONNECT_STACK" ConnectInstanceId)
if [[ -z "$INSTANCE_ARN" || "$INSTANCE_ARN" == "None" ]]; then
  echo "ERROR: could not find $CONNECT_STACK ConnectInstanceArn in $R. Deploy the connect stack first." >&2
  exit 1
fi

# Guard: an instance can have only ONE Q in Connect domain. Refuse if one exists.
EXISTING=$(aws connect list-integration-associations --region "$R" --instance-id "$INSTANCE_ID" \
  --query "IntegrationAssociationSummaryList[?IntegrationType=='WISDOM_ASSISTANT'].IntegrationArn | [0]" \
  --output text 2>/dev/null)
if [[ -n "$EXISTING" && "$EXISTING" != "None" ]]; then
  echo "ERROR: instance $INSTANCE_ID already has a Q in Connect domain associated:" >&2
  echo "       $EXISTING" >&2
  echo "An instance can have only one domain. Remove the existing one first, or skip this step" >&2
  echo "(the running environment already has its domain). Refusing to create a conflicting domain." >&2
  exit 1
fi

echo "    INSTANCE_ARN=$INSTANCE_ARN"
echo "==> Installing module dependencies"
npm install

echo "==> Deploying AnyCompanyPayQicDomainStack"
DOMAIN_ARGS=(-c deployQicDomain=true -c envName="$ENV_NAME" -c connectInstanceArn="$INSTANCE_ARN")
[[ -n "${DOMAIN_NAME:-}" ]] && DOMAIN_ARGS+=(-c domainName="$DOMAIN_NAME")
npx cdk deploy AnyCompanyPayQicDomainStack --require-approval never --outputs-file cdk-outputs-qic-domain.json "${DOMAIN_ARGS[@]}"

echo
echo "=== Done. Q in Connect domain (assistant) created + associated to the instance. ==="
echo "Next: the MCP server registration (console-only) and provision-ai-agent.sh."
