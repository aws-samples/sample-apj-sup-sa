#!/usr/bin/env bash

PIPELINE_PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AWS_REGION="${AWS_REGION:-ap-northeast-2}"
export STACK_NAME="${STACK_NAME:-sagemaker-xgboost-lab}"
export MLFLOW_APP_NAME="${MLFLOW_APP_NAME:-sagemaker-xgboost-lab}"
export MODEL_PACKAGE_GROUP_NAME="${MODEL_PACKAGE_GROUP_NAME:-sagemaker-xgboost-lab-pipeline}"
export PROCESSING_IMAGE_REPOSITORY="${PROCESSING_IMAGE_REPOSITORY:-sagemaker-xgboost-pipeline-processing}"
export IMAGE_TAG="${IMAGE_TAG:-uci-v1}"

stack_output() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" --output text
}

load_shared_resources() {
  S3_BUCKET="$(stack_output ArtifactBucketName)"
  SAGEMAKER_EXECUTION_ROLE_ARN="$(stack_output SageMakerExecutionRoleArn)"
  MLFLOW_APP_ARN="$(
    aws sagemaker list-mlflow-apps --region "$AWS_REGION" \
      --query "Summaries[?Name=='${MLFLOW_APP_NAME}' && (Status=='Created' || Status=='Updated')].Arn | [0]" \
      --output text
  )"
  for value in "$S3_BUCKET" "$SAGEMAKER_EXECUTION_ROLE_ARN" "$MLFLOW_APP_ARN"; do
    if [[ -z "$value" || "$value" == "None" ]]; then
      printf 'Prepare the shared infrastructure in sm-xgboost-lab first.\n' >&2
      return 1
    fi
  done
  local account_id
  account_id="$(aws sts get-caller-identity --query Account --output text)"
  PROCESSING_IMAGE_URI="${account_id}.dkr.ecr.${AWS_REGION}.amazonaws.com/${PROCESSING_IMAGE_REPOSITORY}:${IMAGE_TAG}"
  export S3_BUCKET SAGEMAKER_EXECUTION_ROLE_ARN MLFLOW_APP_ARN PROCESSING_IMAGE_URI
}
