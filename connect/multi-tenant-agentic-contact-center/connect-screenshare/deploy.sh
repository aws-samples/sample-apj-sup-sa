#!/bin/bash
# Deploy the OPT-IN live screen-sharing module, then give agent1 / admin's routing
# profile (anycompany-pay-chat) the screen-share queue on the VOICE channel.
#
# Additive: a voice queue + hours, a web-call flow, the anycompany-pay-video-agent
# security profile, the POST /screenshare/start API and its runtime-config entry
# (screenShareApiUrl, which turns on the "Share screen" button in the merchant app).
# The app image must include the screen-share UI (redeploy AnyCompanyPayAppStack).
#
# Prereqs: AnyCompanyPayAppStack + AnyCompanyPayConnectStack deployed.
# Env: R / AWS_REGION, ENV_NAME, CUSTOMER_VIDEO=1 (also let merchants send camera
#      video), WIRE=0 (don't touch the connect stack), UNWIRE=1 (remove the queue
#      from the anycompany-pay-chat profile and exit).
set -euo pipefail
cd "$(dirname "$0")"

R="${R:-${AWS_REGION:-${CDK_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-west-2)}}}"
ENV_NAME="${ENV_NAME:-}"
export CDK_DEFAULT_REGION="$R" AWS_REGION="$R"
export CDK_DEFAULT_ACCOUNT="${CDK_DEFAULT_ACCOUNT:-$(aws sts get-caller-identity --query Account --output text)}"
SFX="${ENV_NAME:+-${ENV_NAME}}"
APP_STACK="AnyCompanyPayAppStack${SFX}"
CONNECT_STACK="AnyCompanyPayConnectStack${SFX}"
STACK="AnyCompanyPayConnectScreenShareStack${SFX}"

get_out() {
  aws cloudformation describe-stacks --region "$R" --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" --output text 2>/dev/null || true
}
require() {
  if [[ -z "$2" || "$2" == "None" ]]; then echo "ERROR: could not discover '$1' in $R." >&2; exit 1; fi
}

if [[ "${UNWIRE:-0}" == "1" ]]; then
  R="$R" ENV_NAME="$ENV_NAME" SCREENSHARE=off bash ../infra/deploy-connect-stack.sh
  echo "=== anycompany-pay-chat no longer takes screen-share calls ($STACK left deployed). ==="
  exit 0
fi

echo "==> Region: $R   Env: ${ENV_NAME:-<none>}   Account: $CDK_DEFAULT_ACCOUNT"
INSTANCE_ARN=$(get_out "$CONNECT_STACK" ConnectInstanceArn); require ConnectInstanceArn "$INSTANCE_ARN"
CASES_DOMAIN_ID=$(get_out "$CONNECT_STACK" CasesDomainId); require CasesDomainId "$CASES_DOMAIN_ID"
POOL=$(get_out "$APP_STACK" UserPoolId); require UserPoolId "$POOL"
CLIENT=$(get_out "$APP_STACK" UserPoolClientId); require UserPoolClientId "$CLIENT"
DIST=$(get_out "$APP_STACK" CloudFrontUrl); require CloudFrontUrl "$DIST"
PARAM=$(get_out "$APP_STACK" RuntimeConfigParam); require RuntimeConfigParam "$PARAM"
CLUSTER=$(get_out "$APP_STACK" ClusterName); require ClusterName "$CLUSTER"
SERVICE=$(get_out "$APP_STACK" ServiceName); require ServiceName "$SERVICE"

npm install --silent
echo "==> Deploying $STACK"
npx cdk deploy "$STACK" --require-approval never --outputs-file cdk-outputs.json \
  -c envName="$ENV_NAME" -c connectInstanceArn="$INSTANCE_ARN" -c casesDomainId="$CASES_DOMAIN_ID" \
  -c userPoolId="$POOL" -c userPoolClientId="$CLIENT" -c distUrl="${DIST%/}" \
  -c runtimeConfigParam="$PARAM" -c clusterName="$CLUSTER" -c serviceName="$SERVICE" \
  -c customerVideo="$([[ "${CUSTOMER_VIDEO:-0}" == "1" ]] && echo true || echo false)"

if [[ "${WIRE:-1}" == "1" ]]; then
  R="$R" ENV_NAME="$ENV_NAME" SCREENSHARE=on bash ../infra/deploy-connect-stack.sh
fi

echo
echo "=== Done. Screen-share API: $(get_out "$STACK" ScreenShareApiUrl) ==="
echo "Next: bash provision-video-agents.sh   (VideoContact.Access for the agents)"
