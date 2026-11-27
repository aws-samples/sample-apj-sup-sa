#!/usr/bin/env bash
# Builds the custom image and pushes it to Amazon ECR.
#
# Even for GPU (NVIDIA) training, --platform stays linux/amd64.
#   --platform means the *CPU architecture* (amd64 vs arm64); it has nothing to do with GPUs.
#   SageMaker GPU instances (ml.g4dn, ml.g5, ml.p3/p4, etc.) all have x86_64 (amd64) CPUs,
#   so building for linux/amd64 is correct. (Exception: arm64 GPU instances such as Graviton+GPU
#   use linux/arm64, but they are rare for SageMaker training.)
#   What changes for GPU is not --platform but the *image contents*:
#     1) Use a CUDA-enabled base image (e.g. nvidia/cuda:*-runtime or the AWS PyTorch GPU DLC)
#     2) Install a CUDA build of torch, not the CPU build (Dockerfile --index-url -> cu121, etc.)
#     3) nvidia-docker compatible (the host provides the NVIDIA driver; the image needs only CUDA)
#
# Usage:
#   ./build_and_push.sh              # default repo name (byoc-mnist), Region auto-detected
#   ./build_and_push.sh my-repo      # custom repo name
set -euo pipefail

REPO_NAME="${1:-byoc-mnist}"
TAG="latest"

# Auto-detect account/Region (with a helpful message on failure)
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region || true)}}"
if [[ -z "${REGION}" ]]; then
  echo "Could not find a region. Set a default region with 'aws configure' or set AWS_REGION." >&2
  exit 1
fi

IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}:${TAG}"
echo "Target image: ${IMAGE_URI}"

# Check the Docker daemon
if ! docker info >/dev/null 2>&1; then
  echo "The Docker daemon is not running. Start Docker Desktop first." >&2
  exit 1
fi

# 1) Log in to ECR
echo "==> ECR login"
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# 2) Create the repository (ignored if it already exists)
echo "==> Prepare ECR repository"
aws ecr describe-repositories --repository-names "${REPO_NAME}" --region "${REGION}" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "${REPO_NAME}" --region "${REGION}" >/dev/null

# 3) Build (must be linux/amd64!) → tag → push
echo "==> Build (linux/amd64)"
docker build --platform linux/amd64 -t "${REPO_NAME}:${TAG}" container/

echo "==> Tag & push"
docker tag "${REPO_NAME}:${TAG}" "${IMAGE_URI}"
docker push "${IMAGE_URI}"

echo ""
echo "✅ Done: ${IMAGE_URI}"
echo "Now run launch.py with IMAGE_URI set to the value above (it is composed automatically by default):"
echo "  uv run launch.py --dry-run"
