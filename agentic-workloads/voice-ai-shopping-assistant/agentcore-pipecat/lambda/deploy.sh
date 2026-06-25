#!/bin/bash

# Deploy the /start Lambda and expose it via a Function URL.
# Reads AGENT_RUNTIME_ARN + DAILY_ROOM_URL from lambda/start.env (written by launch.sh)
# or from agent/.env (for DAILY_ROOM_URL). Creates an IAM role allowing
# bedrock-agentcore:InvokeAgentRuntime, packages handler.py, and configures env + URL.

set -e
cd "$(dirname "$0")/.."   # repo root: agentcore-pipecat/

FUNCTION_NAME="${FUNCTION_NAME:-summit-start}"
RUNTIME="python3.13"
HANDLER="handler.handler"
START_ENV="./lambda/start.env"

# --- Region + credentials from agent/.env ---
[ -f "./agent/.env" ] && { set -a; source ./agent/.env; set +a; }
AWS_REGION="${AWS_REGION:-ap-southeast-2}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# --- Resolve runtime env values ---
[ -f "$START_ENV" ] && { set -a; source "$START_ENV"; set +a; }
if [ -z "$AGENT_RUNTIME_ARN" ]; then
    echo "❌ AGENT_RUNTIME_ARN not set. Run ./scripts/launch.sh first (writes $START_ENV)."
    exit 1
fi
if [ -z "$DAILY_ROOM_URL" ]; then
    echo "❌ DAILY_ROOM_URL not set (in $START_ENV or agent/.env)."
    exit 1
fi
ALLOWED_ORIGIN="${ALLOWED_ORIGIN:-*}"
# Default AgentCore Memory actor id (sourced from agent/.env). The handler passes
# this as the invocation payload's user_id so long-term prefs are keyed correctly.
DEMO_USER_ID="${DEMO_USER_ID:-demo-user}"

# --- IAM role for the Lambda ---
ROLE_NAME="summit-start-lambda-role"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
cat > /tmp/lambda-trust.json << 'EOF'
{ "Version": "2012-10-17", "Statement": [
  { "Effect": "Allow", "Principal": { "Service": "lambda.amazonaws.com" }, "Action": "sts:AssumeRole" } ] }
EOF

aws iam get-role --role-name "$ROLE_NAME" &>/dev/null || {
    echo "Creating Lambda role $ROLE_NAME ..."
    aws iam create-role --role-name "$ROLE_NAME" \
        --assume-role-policy-document file:///tmp/lambda-trust.json >/dev/null
    aws iam attach-role-policy --role-name "$ROLE_NAME" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
}

cat > /tmp/lambda-invoke.json << EOF
{ "Version": "2012-10-17", "Statement": [
  { "Effect": "Allow",
    "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
    "Resource": ["${AGENT_RUNTIME_ARN}", "${AGENT_RUNTIME_ARN}/*"] } ] }
EOF
aws iam put-role-policy --role-name "$ROLE_NAME" \
    --policy-name InvokeAgentRuntime --policy-document file:///tmp/lambda-invoke.json
echo "Waiting for IAM role to propagate..."
sleep 10

# --- Package ---
rm -f /tmp/summit-start.zip
( cd lambda && zip -q /tmp/summit-start.zip handler.py )

ENV_VARS="Variables={AGENT_RUNTIME_ARN=$AGENT_RUNTIME_ARN,DAILY_ROOM_URL=$DAILY_ROOM_URL,ALLOWED_ORIGIN=$ALLOWED_ORIGIN,DEMO_USER_ID=$DEMO_USER_ID}"

# --- Create or update function ---
if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$AWS_REGION" &>/dev/null; then
    echo "Updating existing function $FUNCTION_NAME ..."
    aws lambda update-function-code --function-name "$FUNCTION_NAME" \
        --zip-file fileb:///tmp/summit-start.zip --region "$AWS_REGION" >/dev/null
    aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$AWS_REGION"
    aws lambda update-function-configuration --function-name "$FUNCTION_NAME" \
        --timeout 30 --environment "$ENV_VARS" --region "$AWS_REGION" >/dev/null
else
    echo "Creating function $FUNCTION_NAME ..."
    aws lambda create-function --function-name "$FUNCTION_NAME" \
        --runtime "$RUNTIME" --handler "$HANDLER" --role "$ROLE_ARN" \
        --timeout 30 --zip-file fileb:///tmp/summit-start.zip \
        --environment "$ENV_VARS" --region "$AWS_REGION" >/dev/null
    aws lambda wait function-active --function-name "$FUNCTION_NAME" --region "$AWS_REGION"
fi

# --- Exposure is via API Gateway (managed separately), NOT a Lambda Function URL ---
# A public Lambda Function URL (AuthType=NONE) was previously created here and
# flagged as a Sev2. It has been removed and replaced with an API Gateway REST
# API ("summit-start-api"). This script intentionally does NOT (re)create a
# Function URL — doing so would regress that security fix.
#
# If a stale Function URL somehow exists, remove it:
#   aws lambda delete-function-url-config --function-name "$FUNCTION_NAME" --region "$AWS_REGION"

# Safety guard: refuse to leave a public Function URL in place.
if aws lambda get-function-url-config --function-name "$FUNCTION_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
    echo "⚠️  A Lambda Function URL exists on $FUNCTION_NAME — deleting it to keep the Sev2 fix intact."
    aws lambda delete-function-url-config --function-name "$FUNCTION_NAME" --region "$AWS_REGION" || true
    aws lambda remove-permission --function-name "$FUNCTION_NAME" \
        --statement-id FunctionURLAllowPublicAccess --region "$AWS_REGION" 2>/dev/null || true
fi

rm -f /tmp/lambda-trust.json /tmp/lambda-invoke.json /tmp/summit-start.zip

API_ID=$(aws apigateway get-rest-apis --region "$AWS_REGION" \
    --query "items[?name=='summit-start-api'].id | [0]" --output text 2>/dev/null)
echo ""
echo "✅ /start Lambda code/config deployed (no Function URL created)."
if [ -n "$API_ID" ] && [ "$API_ID" != "None" ]; then
    echo "   Exposed via API Gateway: https://$API_ID.execute-api.$AWS_REGION.amazonaws.com/prod"
else
    echo "   Expose it via API Gateway (REST API + Lambda proxy) — do NOT add a Function URL."
fi
