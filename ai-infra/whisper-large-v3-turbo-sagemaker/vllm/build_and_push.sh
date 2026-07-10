#!/usr/bin/env bash
#
# Build the vLLM-based Whisper inference image and push it to ECR.
#
# Run from anywhere; the script cd's to the repo root (build context).
#
# Usage:
#   vllm/build_and_push.sh [REGION] [REPO_NAME] [TAG]
# Defaults: REGION=ap-south-1, REPO_NAME=whisper-vllm, TAG=latest

set -euo pipefail
cd "$(dirname "$0")/.."   # repo root = build context

REGION="${1:-ap-south-1}"
REPO_NAME="${2:-whisper-vllm}"
TAG="${3:-latest}"

# Validate inputs: must be non-empty and contain only safe characters.
for var in REGION REPO_NAME TAG; do
  val="${!var}"
  if [[ -z "${val}" ]]; then
    echo "ERROR: ${var} must not be empty." >&2; exit 1
  fi
  if [[ ! "${val}" =~ ^[a-zA-Z0-9._/-]+$ ]]; then
    echo "ERROR: ${var} contains invalid characters: ${val}" >&2; exit 1
  fi
done
# CUDA 12.4 image -- compatible with SageMaker g5 host driver. See vllm/Dockerfile.
BASE_IMAGE="vllm/vllm-openai:v0.8.5.post1"

PY="${PY:-python3}"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
TARGET_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
TARGET_IMAGE="${TARGET_REGISTRY}/${REPO_NAME}:${TAG}"

echo "Region:        ${REGION}"
echo "Base image:    ${BASE_IMAGE} (Docker Hub)"
echo "Target image:  ${TARGET_IMAGE}"

if [ ! -f "model_artifacts/snapshot/config.json" ]; then
  echo "==> Downloading model snapshot..."
  "${PY}" common/download_model.py
else
  echo "==> Model snapshot already present, skipping download."
fi

echo "==> Logging in to target ECR (${ACCOUNT_ID})..."
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${TARGET_REGISTRY}"

if ! aws ecr describe-repositories --region "${REGION}" --repository-names "${REPO_NAME}" >/dev/null 2>&1; then
  echo "==> Creating ECR repo ${REPO_NAME}..."
  aws ecr create-repository --region "${REGION}" --repository-name "${REPO_NAME}" \
    --image-scanning-configuration scanOnPush=true >/dev/null
fi

echo "==> Building and pushing (linux/amd64, no attestations)..."
docker buildx inspect whisper-builder >/dev/null 2>&1 || docker buildx create --name whisper-builder >/dev/null
docker buildx use whisper-builder

docker buildx build \
  --platform linux/amd64 \
  --provenance=false \
  --sbom=false \
  --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
  --file vllm/Dockerfile \
  --tag "${TARGET_IMAGE}" \
  --push \
  .

echo ""
echo "Pushed: ${TARGET_IMAGE}"
echo "Deploy: python common/deploy_from_ecr.py --image-uri ${TARGET_IMAGE} \\"
echo "          --region ${REGION} --role <role-arn> --endpoint-name whisper-vllm"
