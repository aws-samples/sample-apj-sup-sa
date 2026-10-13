#!/usr/bin/env bash
# Deploy the JWT-gated Pipecat Cloud start proxy (API Gateway + Cognito authorizer).
#
# NOTE: for the full pipecat-cloud setup, use scripts/deploy_voice_pcc.sh — it
# runs this proxy deploy AND the PCC agent, secret set, key fill, and UI rewire.
# This script just deploys the proxy stack itself. The PCC key is NOT passed here;
# the stack creates a Secrets Manager placeholder that gets filled post-deploy.
#
# Reads Cognito IDs from the deployed CognitoStack so the proxy validates tokens
# from the SAME user pool the dashboard logs into.
set -euo pipefail

REGION="${AWS_REGION:-us-west-2}"
ENV_NAME="${ENV_NAME:-agentic-analytics}"
STACK="${STACK:-agentic-analytics-voice}"
MAIN_STACK="${MAIN_STACK:-agentic-analytics-demo}"
AGENT_NAME="${PCC_AGENT_NAME:-voice-analytics-agent}"
ALLOWED_ORIGIN="${ALLOWED_ORIGIN:-*}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> Reading Cognito IDs from ${MAIN_STACK} nested CognitoStack"
COG_STACK="$(aws cloudformation list-stack-resources --stack-name "$MAIN_STACK" --region "$REGION" \
  --query "StackResourceSummaries[?ResourceType=='AWS::CloudFormation::Stack' && contains(LogicalResourceId,'Cognito')].PhysicalResourceId" \
  --output text)"
POOL_ID="$(aws cloudformation describe-stacks --stack-name "$COG_STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text)"
CLIENT_ID="$(aws cloudformation describe-stacks --stack-name "$COG_STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='UserLoginClientId'].OutputValue" --output text)"
echo "    UserPoolId=${POOL_ID}  ClientId=${CLIENT_ID}"

echo "==> Bundling Lambda (index.py + python-jose)"
rm -rf "$HERE/build"; mkdir -p "$HERE/build"
cp "$HERE/index.py" "$HERE/build/"
pip3 install --quiet --target "$HERE/build" "python-jose[cryptography]==3.3.0"

echo "==> sam/cfn package + deploy"
aws cloudformation package \
  --template-file "$HERE/voice-proxy-stack.yaml" \
  --s3-bucket "${ARTIFACTS_BUCKET:-agentic-analytics-artifacts}" \
  --s3-prefix voice-proxy \
  --region "$REGION" \
  --output-template-file "$HERE/build/packaged.yaml"

aws cloudformation deploy \
  --template-file "$HERE/build/packaged.yaml" \
  --stack-name "$STACK" \
  --region "$REGION" \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
  --parameter-overrides \
    EnvironmentName="$ENV_NAME" \
    CognitoUserPoolId="$POOL_ID" \
    CognitoAppClientId="$CLIENT_ID" \
    PccAgentName="$AGENT_NAME" \
    AllowedOrigin="$ALLOWED_ORIGIN"

URL="$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='VoiceStartUrl'].OutputValue" --output text)"
echo ""
echo "==> Voice start proxy deployed:"
echo "    VOICE_START_URL = ${URL}"
echo "    NOTE: the PCC key secret is a placeholder — fill it (and rewire the UI)"
echo "    via scripts/deploy_voice_pcc.sh, or: aws secretsmanager put-secret-value"
echo "    --secret-id ${ENV_NAME}-voice-pcc-key --secret-string <pk_...>"
