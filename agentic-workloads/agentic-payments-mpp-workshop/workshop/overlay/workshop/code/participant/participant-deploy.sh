#!/usr/bin/env bash
#
# participant-deploy.sh — builds the Samurai Strands TS container, pushes it
# to ECR, deploys the participant CFN (AgentCore Runtime + Memory), and
# writes the runtime ARN into the SPA's /config.json on CloudFront/S3 so
# the browser can start invoking Samurai directly.
#
# Run from the repo root inside the Code Editor EC2.
set -euo pipefail

if [ -f "./config.env" ]; then
  # shellcheck disable=SC1091
  source ./config.env
fi

: "${AWS_REGION:?AWS_REGION must be set (from config.env)}"
: "${SPA_BUCKET:?SPA_BUCKET must be set (from config.env)}"
: "${SPA_DISTRIBUTION_ID:?SPA_DISTRIBUTION_ID must be set (from config.env)}"
: "${LISTING_BOT_API_URL:?LISTING_BOT_API_URL must be set (from config.env)}"
: "${BUYER_STRIPE_SECRET_ARN:?BUYER_STRIPE_SECRET_ARN must be set (from config.env)}"
: "${MPP_LOGS_TABLE:?MPP_LOGS_TABLE must be set (from config.env)}"
: "${MPP_LOGS_TABLE_ARN:?MPP_LOGS_TABLE_ARN must be set (from config.env)}"

STACK_NAME="${STACK_NAME:-samurai-agentcore}"
ECR_REPO="${ECR_REPO:-samurai-agentcore}"
IMAGE_TAG="${IMAGE_TAG:-v$(date +%Y%m%d%H%M%S)}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}:${IMAGE_TAG}"

echo "=== 1/4 ECR repo ==="
aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$AWS_REGION" \
  >/dev/null 2>&1 || aws ecr create-repository --repository-name "$ECR_REPO" --region "$AWS_REGION"

echo "=== 2/4 Build + push Samurai container image ==="
# Ensure a docker-container buildx builder exists. The default "docker"
# buildx builder cannot cross-build linux/arm64 on an x86 host and will
# fail on the first RUN step with "exec /bin/sh: exec format error".
# AgentCore Runtime requires arm64, so we always want the container
# builder. This is idempotent — safe to re-run on every deploy.
if ! docker buildx inspect wsbuilder >/dev/null 2>&1; then
  docker buildx create --name wsbuilder --driver docker-container --use
  docker buildx inspect --bootstrap >/dev/null
else
  docker buildx use wsbuilder
fi

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

pushd app/samurai-agentcore >/dev/null
docker buildx build --platform linux/arm64 -t "$ECR_URI" --push .
popd >/dev/null

echo "=== 3/4 Deploy participant CFN ==="
aws cloudformation deploy \
  --template-file workshop/code/participant/samurai-agentcore.yaml \
  --stack-name "$STACK_NAME" \
  --parameter-overrides \
      ContainerImageUri="$ECR_URI" \
      ListingBotApiUrl="$LISTING_BOT_API_URL" \
      BuyerStripeSecretArn="$BUYER_STRIPE_SECRET_ARN" \
      MppLogsTableName="$MPP_LOGS_TABLE" \
      MppLogsTableArn="$MPP_LOGS_TABLE_ARN" \
  --capabilities CAPABILITY_NAMED_IAM CAPABILITY_IAM \
  --region "$AWS_REGION"

RUNTIME_ARN="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue | [0]" \
  --output text)"
echo "Samurai Runtime ARN: $RUNTIME_ARN"

echo "=== 4/4 Publish runtime ARN to SPA /config.json ==="
# Read-modify-write: fetch the existing /config.json (written by the
# spa_deployer custom resource with Cognito pool ids, identity pool id,
# region, MPP logs table, etc.) and patch ONLY the samuraiAgentRuntimeArn
# field. Overwriting the whole file would wipe out the Cognito config and
# break SPA login with "Auth UserPool not configured."
TMP_CFG="$(mktemp)"
TMP_OLD="$(mktemp)"
aws s3 cp "s3://${SPA_BUCKET}/config.json" "$TMP_OLD"
jq --arg arn "$RUNTIME_ARN" '. + {samuraiAgentRuntimeArn: $arn}' \
  "$TMP_OLD" > "$TMP_CFG"
aws s3 cp "$TMP_CFG" "s3://${SPA_BUCKET}/config.json" \
  --content-type application/json --cache-control "no-cache, no-store, must-revalidate"
aws cloudfront create-invalidation \
  --distribution-id "$SPA_DISTRIBUTION_ID" --paths "/config.json" >/dev/null
rm "$TMP_CFG" "$TMP_OLD"

echo ""
echo "✓ Done. Samurai SPA now knows the runtime ARN — refresh the browser tab."
