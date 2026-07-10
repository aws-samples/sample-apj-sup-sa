#!/usr/bin/env bash
# Build + push the llm-batch-deploy runtime image to ECR.
#
# Usage:
#   ./scripts/build_and_push.sh <ecr-repo-uri> [tag]
#
# Requires:
#   - Docker daemon running
#   - AWS credentials with ECR push permissions
#   - ~25 GiB free disk
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <ecr-repo-uri> [tag]" >&2
  echo "  e.g. $0 <account-id>.dkr.ecr.us-west-2.amazonaws.com/medgemma-27b-batch latest" >&2
  exit 1
fi

ECR_URI="$1"
TAG="${2:-latest}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

REGISTRY="${ECR_URI%%/*}"
# Parse region out of the ECR URI: <account>.dkr.ecr.<region>.amazonaws.com/<repo>
REGION="$(echo "${ECR_URI}" | awk -F. '{print $4}')"

cd "${PROJECT_ROOT}"

echo "==> Logging Docker into ${REGISTRY} (region=${REGION})"
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${REGISTRY}"

echo "==> Building image for linux/amd64 ..."
docker build \
  --platform linux/amd64 \
  -t "${ECR_URI}:${TAG}" \
  -f src/llm_batch_deploy/runtime/Dockerfile \
  .

echo "==> Pushing ${ECR_URI}:${TAG}"
docker push "${ECR_URI}:${TAG}"

echo ""
echo "Done. Update your stack to use this image:"
echo "  deploy(plan, container_image_uri='${ECR_URI}:${TAG}')"
