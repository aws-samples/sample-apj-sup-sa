#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
cd "$PIPELINE_PROJECT_DIR"
REPOSITORY_NAME="$PROCESSING_IMAGE_REPOSITORY"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${REPOSITORY_NAME}:${IMAGE_TAG}"

if ! aws ecr describe-repositories --repository-names "$REPOSITORY_NAME" \
  --region "$AWS_REGION" >/dev/null 2>&1; then
  aws ecr create-repository --repository-name "$REPOSITORY_NAME" --region "$AWS_REGION" >/dev/null
fi

aws ecr get-login-password --region "$AWS_REGION" |
  docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

docker build --platform linux/amd64 \
  --file images/processing.Dockerfile \
  --tag "$IMAGE_URI" \
  images
docker push "$IMAGE_URI"

printf 'Processing image ready: %s\n' "$IMAGE_URI"
