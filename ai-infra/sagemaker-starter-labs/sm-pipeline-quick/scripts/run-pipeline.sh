#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-northeast-2}"
STACK_NAME="${STACK_NAME:-sagemaker-xgboost-lab}"
MLFLOW_APP_NAME="${MLFLOW_APP_NAME:-sagemaker-xgboost-lab}"
PROCESSING_IMAGE_REPOSITORY="${PROCESSING_IMAGE_REPOSITORY:-sagemaker-xgboost-pipeline-processing}"
IMAGE_TAG="${IMAGE_TAG:-visual-only}"

stack_output() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" \
    --output text
}

account_id="$(aws sts get-caller-identity --query Account --output text)"
mlflow_app_arn="$(
  aws sagemaker list-mlflow-apps \
    --region "$AWS_REGION" \
    --query "Summaries[?Name=='${MLFLOW_APP_NAME}' && (Status=='Created' || Status=='Updated')].Arn | [0]" \
    --output text
)"
if [[ "$mlflow_app_arn" == "None" || -z "$mlflow_app_arn" ]]; then
  printf 'Ready MLflow App not found. Run sm-xgboost-lab/scripts/bootstrap-infra.sh first.\n' >&2
  exit 1
fi

AWS_REGION="$AWS_REGION" \
SAGEMAKER_EXECUTION_ROLE_ARN="$(stack_output SageMakerExecutionRoleArn)" \
S3_BUCKET="$(stack_output ArtifactBucketName)" \
MLFLOW_APP_ARN="$mlflow_app_arn" \
MODEL_PACKAGE_GROUP_NAME="sagemaker-xgboost-lab-pipeline" \
PROCESSING_IMAGE_URI="${account_id}.dkr.ecr.${AWS_REGION}.amazonaws.com/${PROCESSING_IMAGE_REPOSITORY}:${IMAGE_TAG}" \
REDSHIFT_WORKGROUP_NAME="visual-only-redshift-workgroup" \
REDSHIFT_DATABASE="visual_only_database" \
REDSHIFT_UNLOAD_ROLE_ARN="arn:aws:iam::${account_id}:role/visual-only-redshift-unload" \
uv run --no-sync sagemaker-xgboost-pipeline "$@"
