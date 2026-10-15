#!/usr/bin/env bash
# Build the serving image and push it to a private ECR repository, then print IMAGE=<uri>.
#
#   REGION=us-east-2 ACCOUNT=<account-id> ./build.sh
#
# Needs Docker with buildx, curl and the AWS CLI v2, with credentials for ACCOUNT. Build on an x86_64 machine if you
# can: SageMaker GPU instances are x86_64, and on arm64 (Apple silicon) Docker emulates it and the build is slow.
# The image tag is a hash of everything the build uses, so an unchanged image is never rebuilt and a changed one
# never overwrites a tag in use (the repository has immutable tags).
set -euo pipefail

REGION="${REGION:?set REGION}"
ACCOUNT="${ACCOUNT:?set ACCOUNT}"
REPO="${REPO:-torchcast-decision-12b-sagemaker}"
CREATED_BY="torchcast-decision-12b-sagemaker-sample"   # the tag the notebook's clean-up looks for

# TorchCast's serving files at the v1.0.0 release commit, checked against these SHA-256 sums.
TORCHCAST_COMMIT=d7f84468f7c4a21c8aeed52df845d98c59afae04
SERVING_SUMS="4147a39acce54ce7e510dc21ace2b469f584a2cabbd8dd79c95043014c88b5a6  decision_server.py
1dff956ead46f251fe7c60d91b4cf08b241d56a69a45eba835463bf4fc252694  torchcast_shim.py
8a4de5ef4a26a24d8be69c123a6a6622016f487693b0bc8fc683b92f0d7ade97  readout_config.json
430a36f677e68b83a72bb3bbb326812fa4462fbb9a3118b1949c2154cc208215  LICENSE-serving-MIT"

# The AWS vLLM DLC, SageMaker flavour. 763104351884 publishes the DLCs in most commercial Regions; a few opt-in
# Regions use another account (see the AWS Deep Learning Containers documentation).
DLC_ACCOUNT="${DLC_ACCOUNT:-763104351884}"
BASE="${DLC_ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/vllm:0.30.0-gpu-py312-cu130-ubuntu24.04-sagemaker-v1.4"

cd "$(dirname "$0")"
if command -v sha256sum > /dev/null; then sha() { sha256sum "$@"; }; else sha() { shasum -a 256 "$@"; }; fi

rm -rf serving && mkdir serving
for f in decision_server.py torchcast_shim.py readout_config.json LICENSE-serving-MIT; do
  curl -fsSL --proto '=https' --tlsv1.2 -o "serving/$f" \
    "https://raw.githubusercontent.com/Torchcast-AI/torchcast-decision-12b/${TORCHCAST_COMMIT}/serving/$f"
done
(cd serving && printf '%s\n' "$SERVING_SUMS" | sha -c --quiet -) || { echo "serving files do not match"; exit 1; }

TAG="$( { cat Dockerfile serve.py serving/decision_server.py serving/torchcast_shim.py serving/readout_config.json \
          serving/LICENSE-serving-MIT; printf '%s' "$BASE"; } | sha | cut -c1-12)"
IMAGE="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${REPO}:${TAG}"

if ! aws ecr describe-repositories --region "$REGION" --repository-names "$REPO" > /dev/null 2>&1; then
  aws ecr create-repository --region "$REGION" --repository-name "$REPO" --image-tag-mutability IMMUTABLE \
    --image-scanning-configuration scanOnPush=true --encryption-configuration encryptionType=AES256 \
    --tags "Key=created-by,Value=${CREATED_BY}" > /dev/null
  echo "created ECR repository $REPO"
fi

if aws ecr describe-images --region "$REGION" --repository-name "$REPO" --image-ids "imageTag=$TAG" > /dev/null 2>&1; then
  echo "image already in ECR, not rebuilding"
else
  # The password goes to docker on stdin, never on a command line.
  for registry in "${DLC_ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com" "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"; do
    aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$registry"
  done
  # SageMaker accepts only a Docker image manifest v2 schema 2. Recent Docker versions push OCI media types and add
  # provenance and SBOM attestations, which turn the push into an OCI image index. These flags turn all three off.
  docker buildx build --provenance=false --sbom=false --platform linux/amd64 --build-arg BASE_IMAGE="$BASE" \
    --output "type=image,name=${IMAGE},oci-mediatypes=false,push=true" .
fi

MEDIA="$(aws ecr batch-get-image --region "$REGION" --repository-name "$REPO" --image-ids "imageTag=$TAG" \
  --query 'images[0].imageManifestMediaType' --output text)"
echo "manifest media type: $MEDIA"
[ "$MEDIA" = "application/vnd.docker.distribution.manifest.v2+json" ] || { echo "SageMaker cannot use $MEDIA"; exit 1; }
echo "IMAGE=$IMAGE"
