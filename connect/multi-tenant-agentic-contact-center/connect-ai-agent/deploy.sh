#!/bin/bash
# Deploy the AnyCompanyPay agentic self-service module: the tenant-isolated transaction
# Q&A Lambda (MCP tool), the AgentCore Gateway request interceptor, and the
# AgentCore Gateway + Lambda target (AWS::BedrockAgentCore::* resources).
#
# Nothing here is hardcoded to a region or environment. The region comes from the
# environment (AWS_REGION / CDK_DEFAULT_REGION / your configured default) and the
# Aurora VPC/subnets, the OpenSearch Serverless collection, and the Connect
# instance alias are DISCOVERED from the upstream stack outputs, then passed via
# -c. That is what lets the same module deploy to a second region (e.g. Tokyo)
# with no code edits.
#
# Prereqs (SAME account + region):
#   - AnyCompanyPayAuroraStack deployed (VPC + subnets for the tool Lambda).
#   - AnyCompanyPayZeroEtlStack deployed (the private OpenSearch Serverless collection).
#   - AnyCompanyPayConnectStack-<env> deployed (the Connect instance; its alias is the
#     Gateway's OIDC issuer for CUSTOM_JWT inbound auth).
#   - Valid AWS credentials (run `aws login` if the session expired).
#
# Env overrides:
#   R / AWS_REGION / CDK_DEFAULT_REGION   target region (default: configured region)
#   ENV_NAME                              environment discriminator (default: v2)
set -euo pipefail
cd "$(dirname "$0")"

# --- Resolve region from the environment (never hardcoded) ---------------------
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

echo "==> Region: $R   Env: $ENV_NAME   Account: $CDK_DEFAULT_ACCOUNT"

get_out() {
  aws cloudformation describe-stacks --region "$R" --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" --output text 2>/dev/null
}
require() {
  if [[ -z "$2" || "$2" == "None" ]]; then
    echo "ERROR: could not discover '$1'. Is the upstream stack deployed in $R?" >&2
    exit 1
  fi
}

echo "==> Discovering Aurora module outputs (AnyCompanyPayAuroraStack)"
VPC=$(get_out AnyCompanyPayAuroraStack VpcId); require "VpcId" "$VPC"
SUBNETS=$(aws ec2 describe-subnets --region "$R" --filters Name=vpc-id,Values="$VPC" \
  --query "Subnets[].SubnetId" --output text | tr '\t' ',')
require "subnets" "$SUBNETS"

echo "==> Discovering zero-ETL module outputs (AnyCompanyPayZeroEtlStack)"
COLLECTION_ENDPOINT=$(get_out AnyCompanyPayZeroEtlStack CollectionEndpoint); require "CollectionEndpoint" "$COLLECTION_ENDPOINT"
COLLECTION_NAME=$(get_out AnyCompanyPayZeroEtlStack CollectionName)
[[ -z "$COLLECTION_NAME" || "$COLLECTION_NAME" == "None" ]] && COLLECTION_NAME="anycompany-pay-tx"

echo "==> Discovering connect module outputs ($CONNECT_STACK)"
# The Gateway's OIDC issuer is the Connect instance alias. Prefer parsing it from
# the CcpUrl output; fall back to the deterministic anycompany-pay-<account>-<env> alias.
CCP_URL=$(get_out "$CONNECT_STACK" CcpUrl)
if [[ -n "$CCP_URL" && "$CCP_URL" != "None" ]]; then
  CONNECT_ALIAS="$(echo "$CCP_URL" | sed -E 's#^https?://([^.]+)\..*#\1#')"
else
  CONNECT_ALIAS="anycompany-pay-${CDK_DEFAULT_ACCOUNT}${ENV_NAME:+-${ENV_NAME}}"
fi
require "connect alias" "$CONNECT_ALIAS"

# Connect instance ARN — needed by the AI-agent security profile (the invocation
# gate for the gateway MCP tool). Discovered from the connect stack output.
CONNECT_INSTANCE_ARN=$(get_out "$CONNECT_STACK" ConnectInstanceArn)
require "ConnectInstanceArn" "$CONNECT_INSTANCE_ARN"

echo "    VPC=$VPC"
echo "    SUBNETS=$SUBNETS"
echo "    COLLECTION=$COLLECTION_NAME @ $COLLECTION_ENDPOINT"
echo "    CONNECT_ALIAS=$CONNECT_ALIAS"
echo "    CONNECT_INSTANCE_ARN=$CONNECT_INSTANCE_ARN"

echo "==> Installing module dependencies"
npm install

# The AI-agent security profile is OPT-IN and skipped by default. Its MCP grant
# references the gateway's namespace, which Connect only recognises AFTER you
# register the gateway as an MCP server in the console (see README §2).
# Creating it during the first deploy fails ("Application namespace ... is not
# valid"). After the console registration, re-run with WITH_SECURITY_PROFILE=1 to
# have CDK create it (or let provision-ai-agent.sh create + assign it).
WITH_SECURITY_PROFILE="${WITH_SECURITY_PROFILE:-}"

deploy_once() {
  npx cdk deploy AnyCompanyPayConnectAiAgentStack --require-approval never --outputs-file cdk-outputs.json \
    -c envName="$ENV_NAME" \
    -c dbVpcId="$VPC" \
    -c dbSubnetIds="$SUBNETS" \
    -c collectionEndpoint="$COLLECTION_ENDPOINT" \
    -c collectionName="$COLLECTION_NAME" \
    -c connectAlias="$CONNECT_ALIAS" \
    -c connectInstanceArn="$CONNECT_INSTANCE_ARN" \
    -c withSecurityProfile="$WITH_SECURITY_PROFILE"
}

# The AgentCore GatewayTarget can transiently fail with "Lambda ... is not ready
# or has a resource conflict" right after the tool Lambda is created (service-side
# eventual consistency). CloudFormation rolls back; a re-deploy once the Lambda has
# settled almost always succeeds. Retry a couple of times before giving up.
echo "==> Deploying AnyCompanyPayConnectAiAgentStack"
attempt=1; max=3
until deploy_once; do
  if [ "$attempt" -ge "$max" ]; then
    echo "ERROR: cdk deploy failed after $max attempts." >&2
    exit 1
  fi
  echo "  attempt $attempt failed — waiting 45s for eventual consistency, then retrying..." >&2
  attempt=$((attempt + 1)); sleep 45
done

echo
echo "=== Done. AgentCore Gateway + tenant-isolated transaction tool deployed. ==="
echo "Next: register the Gateway as an MCP server + wire the orchestrator"
echo "(see README §2 and provision-ai-agent.sh),"
echo "then create the MCP tool grant (re-run with WITH_SECURITY_PROFILE=1, or via provision-ai-agent.sh)."
