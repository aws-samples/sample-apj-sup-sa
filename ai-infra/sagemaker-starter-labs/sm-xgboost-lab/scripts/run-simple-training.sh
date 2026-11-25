#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-northeast-2}"
STACK_NAME="${STACK_NAME:-sagemaker-xgboost-lab}"

stack_output() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" \
    --output text
}

AWS_REGION="$AWS_REGION" \
SAGEMAKER_EXECUTION_ROLE_ARN="$(stack_output SageMakerExecutionRoleArn)" \
S3_BUCKET="$(stack_output ArtifactBucketName)" \
uv run --no-sync sagemaker-xgboost-simple "$@"
