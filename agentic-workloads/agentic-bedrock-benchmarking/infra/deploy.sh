#!/usr/bin/env bash
# agentic-bedrock-benchmarking deploy script.
#
# Uses the AWS_PROFILE you set (default: 'default') for stack ops + ECR push.
# The DEPLOYED app uses the runtime role (NOT this profile) for Bedrock calls.
#
# Run from anywhere; this script cd's to the project root.

set -euo pipefail

# -------- config --------
APP_NAME="${APP_NAME:-agentic-bedrock-benchmarking}"
REGION="${REGION:-ap-south-1}"
PROFILE="${AWS_PROFILE:-default}"
STACK_NAME="${STACK_NAME:-${APP_NAME}}"
TEMPLATE_PATH="$(cd "$(dirname "$0")" && pwd)/agentic-bedrock-benchmarking.yaml"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d-%H%M%S)}"

# -------- helpers --------
log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
err() { printf '\033[31m!! %s\033[0m\n' "$*" >&2; exit 1; }

require() {
  command -v "$1" >/dev/null 2>&1 || err "'$1' not found in PATH"
}

# -------- preflight --------
require docker
require aws

log "using profile: $PROFILE  ·  region: $REGION  ·  tag: $IMAGE_TAG"

ACCOUNT_ID=$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text 2>/dev/null) \
  || err "could not resolve AWS account with profile '$PROFILE'. fix your creds and retry."
log "deploying into account $ACCOUNT_ID"

ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${APP_NAME}"

# -------- step 1: ensure ECR repo exists (chicken-and-egg: stack creates it, but we need to push BEFORE
# App Runner resource is created, otherwise the image tag won't exist). Solution: deploy stack in
# two passes — first create just the ECR repo, then push, then full stack.
# Simpler: pre-create the ECR repo via aws cli if it doesn't exist; the stack will adopt it.

log "step 1/4 · ensure ECR repo exists"
if ! aws ecr describe-repositories \
      --profile "$PROFILE" --region "$REGION" \
      --repository-names "$APP_NAME" >/dev/null 2>&1; then
  log "  creating ECR repo: $APP_NAME"
  aws ecr create-repository \
    --profile "$PROFILE" --region "$REGION" \
    --repository-name "$APP_NAME" \
    --image-scanning-configuration scanOnPush=true >/dev/null
else
  log "  ECR repo already exists"
fi

# -------- step 2: build & push image --------
log "step 2/4 · docker build"
( cd "$PROJECT_ROOT" && docker build --platform linux/amd64 -t "${APP_NAME}:${IMAGE_TAG}" . )

log "step 2/4 · docker login to ECR"
aws ecr get-login-password --profile "$PROFILE" --region "$REGION" \
  | docker login --username AWS --password-stdin "$ECR_URI"

log "step 2/4 · docker tag + push: ${ECR_URI}:${IMAGE_TAG}"
docker tag "${APP_NAME}:${IMAGE_TAG}" "${ECR_URI}:${IMAGE_TAG}"
docker tag "${APP_NAME}:${IMAGE_TAG}" "${ECR_URI}:latest"
docker push "${ECR_URI}:${IMAGE_TAG}"
docker push "${ECR_URI}:latest"

# -------- step 3: deploy CloudFormation stack --------
log "step 3/4 · cloudformation deploy"
aws cloudformation deploy \
  --profile "$PROFILE" --region "$REGION" \
  --stack-name "$STACK_NAME" \
  --template-file "$TEMPLATE_PATH" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      AppName="$APP_NAME" \
      Region="$REGION" \
      ImageTag="$IMAGE_TAG"

# -------- step 4: print outputs --------
log "step 4/4 · stack outputs"
aws cloudformation describe-stacks \
  --profile "$PROFILE" --region "$REGION" \
  --stack-name "$STACK_NAME" \
  --query 'Stacks[0].Outputs' \
  --output table

log "done."
log "next steps:"
log "  1. sign up at the AppUrl above or check your inbox for a Cognito invitation email"
log "  2. visit the AppUrl above and sign in with the temp password"
log "  3. set a permanent password when prompted"
