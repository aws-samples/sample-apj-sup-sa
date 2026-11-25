#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-northeast-2}"
STACK_NAME="${STACK_NAME:-sagemaker-xgboost-lab}"

stack_output() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" --output text
}

AWS_REGION="$AWS_REGION" \
S3_BUCKET="$(stack_output ArtifactBucketName)" \
SAGEMAKER_EXECUTION_ROLE_ARN="$(stack_output SageMakerExecutionRoleArn)" \
MODEL_PACKAGE_GROUP_NAME="${MODEL_PACKAGE_GROUP_NAME:-}" \
uv run --no-sync python -m sagemaker_xgboost_lab.batch_transform
