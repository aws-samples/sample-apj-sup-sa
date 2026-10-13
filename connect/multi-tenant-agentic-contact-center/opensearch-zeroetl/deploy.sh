#!/bin/bash
# Deploy the AnyCompanyPay zero-ETL module: Aurora PostgreSQL -> private OpenSearch
# Serverless via an OpenSearch Ingestion pipeline (min 1 / max 2 OCUs), plus the
# merchant transaction Search API and the Commerce write API.
#
# Nothing here is hardcoded to a region or environment: the region comes from the
# environment (AWS_REGION / CDK_DEFAULT_REGION / your configured default) and
# every resource id is DISCOVERED from the upstream stack outputs, then passed to
# the stack via -c. That is what lets the same module deploy to a second region
# (e.g. Tokyo) with no code edits.
#
# Prereqs (in the SAME account + region):
#   - AnyCompanyPayAuroraStack deployed (the database module) WITH logical replication
#     enabled and the writer rebooted (the database module does this).
#   - AnyCompanyPayAppStack-<env> deployed (the app module).
#   - AnyCompanyPayConnectStack-<env> deployed (the connect module) — for the Cases domain.
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
APP_STACK="AnyCompanyPayAppStack${ENV_NAME:+-${ENV_NAME}}"
CONNECT_STACK="AnyCompanyPayConnectStack${ENV_NAME:+-${ENV_NAME}}"

echo "==> Region: $R   Env: $ENV_NAME"

# get_out <stack> <OutputKey> -> value (empty string if missing)
get_out() {
  aws cloudformation describe-stacks --region "$R" --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" --output text 2>/dev/null
}
require() { # require <name> <value>
  if [[ -z "$2" || "$2" == "None" ]]; then
    echo "ERROR: could not discover '$1'. Is the upstream stack deployed in $R?" >&2
    exit 1
  fi
}

echo "==> Discovering Aurora module outputs (AnyCompanyPayAuroraStack)"
VPC=$(get_out AnyCompanyPayAuroraStack VpcId);                require "VpcId" "$VPC"
CLUSTER=$(get_out AnyCompanyPayAuroraStack ClusterIdentifier); require "ClusterIdentifier" "$CLUSTER"
SECRET=$(get_out AnyCompanyPayAuroraStack SecretArn);          require "SecretArn" "$SECRET"
DBNAME=$(get_out AnyCompanyPayAuroraStack DatabaseName);       require "DatabaseName" "$DBNAME"
DBSG=$(get_out AnyCompanyPayAuroraStack DbSecurityGroupId);    require "DbSecurityGroupId" "$DBSG"
DBHOST=$(get_out AnyCompanyPayAuroraStack ClusterEndpoint);    require "ClusterEndpoint" "$DBHOST"
SUBNETS=$(aws ec2 describe-subnets --region "$R" --filters Name=vpc-id,Values="$VPC" \
  --query "Subnets[].SubnetId" --output text | tr '\t' ',')
require "subnets" "$SUBNETS"
CIDR=$(aws ec2 describe-vpcs --region "$R" --vpc-ids "$VPC" --query "Vpcs[0].CidrBlock" --output text)
require "vpc cidr" "$CIDR"

echo "==> Discovering app module outputs ($APP_STACK)"
POOL=$(get_out "$APP_STACK" UserPoolId);        require "UserPoolId" "$POOL"
CLIENT=$(get_out "$APP_STACK" UserPoolClientId); require "UserPoolClientId" "$CLIENT"
DIST=$(get_out "$APP_STACK" CloudFrontUrl);      require "CloudFrontUrl" "$DIST"
ECS_CLUSTER=$(get_out "$APP_STACK" ClusterName); require "ClusterName" "$ECS_CLUSTER"
ECS_SERVICE=$(get_out "$APP_STACK" ServiceName); require "ServiceName" "$ECS_SERVICE"
RUNTIME_PARAM=$(get_out "$APP_STACK" RuntimeConfigParam); require "RuntimeConfigParam" "$RUNTIME_PARAM"

echo "==> Discovering connect module outputs ($CONNECT_STACK)"
CASES_DOMAIN=$(get_out "$CONNECT_STACK" CasesDomainId); require "CasesDomainId" "$CASES_DOMAIN"

echo "    VPC=$VPC  CLUSTER=$CLUSTER  SG=$DBSG"
echo "    HOST=$DBHOST"
echo "    SUBNETS=$SUBNETS  CIDR=$CIDR"
echo "    POOL=$POOL  CLIENT=$CLIENT"
echo "    ECS=$ECS_CLUSTER / $ECS_SERVICE  PARAM=$RUNTIME_PARAM"
echo "    CASES_DOMAIN=$CASES_DOMAIN  DIST=$DIST"

echo "==> Installing module dependencies"
npm install

echo "==> Deploying AnyCompanyPayZeroEtlStack"
npx cdk deploy AnyCompanyPayZeroEtlStack --require-approval never --outputs-file cdk-outputs.json \
  -c envName="$ENV_NAME" \
  -c dbVpcId="$VPC" \
  -c dbSubnetIds="$SUBNETS" \
  -c dbSgId="$DBSG" \
  -c dbClusterId="$CLUSTER" \
  -c dbSecretArn="$SECRET" \
  -c dbName="$DBNAME" \
  -c dbHost="$DBHOST" \
  -c dbVpcCidr="$CIDR" \
  -c casesDomainId="$CASES_DOMAIN" \
  -c cognitoPoolId="$POOL" \
  -c cognitoClientId="$CLIENT" \
  -c distUrl="$DIST" \
  -c runtimeConfigParamName="$RUNTIME_PARAM" \
  -c ecsCluster="$ECS_CLUSTER" \
  -c ecsService="$ECS_SERVICE"

echo
echo "=== Done. The OpenSearch Serverless collection is PRIVATE (VPC-only). ==="
echo "The OSIS pipeline performs the initial snapshot then streams changes (CDC)."
