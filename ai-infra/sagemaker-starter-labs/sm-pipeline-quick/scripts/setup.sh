#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
cd "$PIPELINE_PROJECT_DIR"

if ! docker info >/dev/null 2>&1; then
  printf 'Start the Docker engine before running setup.\n' >&2
  exit 1
fi
uv sync --frozen --extra dev
load_shared_resources

printf '\n[1/4] Prepare Pipeline permissions and Model Package Group\n'
aws cloudformation deploy \
  --template-file infra/pipeline.yaml \
  --stack-name "${PIPELINE_STACK_NAME:-sagemaker-xgboost-lab-pipeline}" \
  --parameter-overrides \
    "BucketName=${S3_BUCKET}" \
    "ExecutionRoleName=${SAGEMAKER_EXECUTION_ROLE_ARN##*/}" \
    "ExecutionRoleArn=${SAGEMAKER_EXECUTION_ROLE_ARN}" \
    "ModelPackageGroupName=${MODEL_PACKAGE_GROUP_NAME}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --region "$AWS_REGION"
uv run --no-sync sagemaker-xgboost-pipeline-bootstrap --region "$AWS_REGION"

printf '\n[2/4] Build and push the Processing image\n'
./scripts/build-processing-image.sh

printf '\n[3/4] Download the public UCI data and upload the input snapshot\n'
uv run --project ../sm-xgboost-lab --frozen python scripts/prepare-input.py \
  --output-dir artifacts/input --bucket "$S3_BUCKET" --region "$AWS_REGION"

printf '\n[4/4] Register the Pipeline\n'
./scripts/run-pipeline.sh --upsert
printf '\nOpen sagemaker-xgboost-lab-pipeline in Studio and start an execution.\n'
