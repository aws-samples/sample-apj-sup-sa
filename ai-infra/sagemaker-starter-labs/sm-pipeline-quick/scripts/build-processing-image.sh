#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-northeast-2}"
REPOSITORY_NAME="${PROCESSING_IMAGE_REPOSITORY:-sagemaker-xgboost-pipeline-processing}"
IMAGE_TAG="${IMAGE_TAG:-dev}"
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
  .
docker push "$IMAGE_URI"

printf 'export PROCESSING_IMAGE_URI=%s\n' "$IMAGE_URI"
