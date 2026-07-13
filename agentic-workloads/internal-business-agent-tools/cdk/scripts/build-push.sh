#!/bin/bash
set -euo pipefail

ACCOUNT="${AWS_ACCOUNT:?AWS_ACCOUNT environment variable is required (e.g. AWS_ACCOUNT=123456789012)}"
REGION="${AWS_REGION:-ap-northeast-1}"
REPO_NAME="internalagent/internal-mcp-server"
REPO_URI="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}"
CONTEXT_DIR="$(dirname "$0")/../../mcp-server"

echo "==> Ensuring ECR repository exists..."
aws ecr describe-repositories --repository-names "${REPO_NAME}" --region "${REGION}" ${AWS_PROFILE:+--profile "${AWS_PROFILE}"} >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "${REPO_NAME}" --image-scanning-configuration scanOnPush=true --region "${REGION}" ${AWS_PROFILE:+--profile "${AWS_PROFILE}"} >/dev/null

echo "==> Logging into ECR..."
aws ecr get-login-password --region "${REGION}" ${AWS_PROFILE:+--profile "${AWS_PROFILE}"} \
  | docker login --username AWS --password-stdin "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

echo "==> Building container image..."
docker build -t "${REPO_URI}:latest" "${CONTEXT_DIR}"

echo "==> Pushing to ECR..."
docker push "${REPO_URI}:latest"

echo "==> Done. Image: ${REPO_URI}:latest"
