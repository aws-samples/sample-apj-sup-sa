#!/usr/bin/env bash
#
# Builds the AnyCompanyPay SPA container image and pushes it to ECR, then prints the
# image URI to pass as the ContainerImageUri parameter to anycompany-pay.yaml.
#
# Usage:
#   ./build-and-push.sh [region] [repo] [tag]
# Defaults: region=<AWS session region>  repo=anycompany-pay-web  tag=latest
set -euo pipefail

# Region from arg 1, else the AWS session/env (never hardcoded).
REGION="${1:-${AWS_REGION:-${CDK_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-west-2)}}}"
if [ -z "$REGION" ]; then
  echo "ERROR: no region. Pass it as arg 1 or set AWS_REGION, e.g. ./build-and-push.sh us-west-2" >&2; exit 1
fi
REPO="${2:-anycompany-pay-web}"
TAG="${3:-latest}"

# Repo root is two levels up from infra/cloudformation/
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE_URI="${REGISTRY}/${REPO}:${TAG}"

echo ">> Ensuring ECR repository '${REPO}' exists in ${REGION}"
aws ecr describe-repositories --repository-names "${REPO}" --region "${REGION}" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "${REPO}" --region "${REGION}" \
       --image-scanning-configuration scanOnPush=true >/dev/null

echo ">> Logging Docker in to ECR"
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${REGISTRY}"

# Base images are pulled from ECR Public; authenticate to avoid rate limits.
aws ecr-public get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin public.ecr.aws

echo ">> Building image (linux/amd64) from ${ROOT}"
docker build --platform linux/amd64 -t "${IMAGE_URI}" "${ROOT}"

echo ">> Pushing ${IMAGE_URI}"
docker push "${IMAGE_URI}"

echo ""
echo "Image pushed. Deploy with:"
echo "  aws cloudformation deploy \\"
echo "    --template-file infra/cloudformation/anycompany-pay.yaml \\"
echo "    --stack-name anycompany-pay \\"
echo "    --capabilities CAPABILITY_IAM \\"
echo "    --region ${REGION} \\"
echo "    --parameter-overrides ContainerImageUri=${IMAGE_URI}"
echo ""
echo "IMAGE_URI=${IMAGE_URI}"
