#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-northeast-2}"
STACK_NAME="${STACK_NAME:-sagemaker-xgboost-lab}"
MLFLOW_APP_NAME="${MLFLOW_APP_NAME:-sagemaker-xgboost-lab}"

stack_output() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" \
    --output text
}

bucket="$(stack_output ArtifactBucketName)"
execution_role_arn="$(stack_output SageMakerExecutionRoleArn)"
mlflow_app_arn="$(
  aws sagemaker list-mlflow-apps \
    --region "$AWS_REGION" \
    --query "Summaries[?Name=='${MLFLOW_APP_NAME}' && (Status=='Created' || Status=='Updated')].Arn | [0]" \
    --output text
)"
if [[ "$mlflow_app_arn" == "None" || -z "$mlflow_app_arn" ]]; then
  printf 'Ready MLflow App not found. Run ./scripts/bootstrap-infra.sh first.\n' >&2
  exit 1
fi

AWS_REGION="$AWS_REGION" \
SAGEMAKER_EXECUTION_ROLE_ARN="$execution_role_arn" \
S3_BUCKET="$bucket" \
MLFLOW_APP_ARN="$mlflow_app_arn" \
uv run --no-sync sagemaker-xgboost-autolog "$@"
