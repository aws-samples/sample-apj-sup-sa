#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-northeast-2}"
STACK_NAME="${STACK_NAME:-sagemaker-xgboost-lab}"
MLFLOW_APP_NAME="${MLFLOW_APP_NAME:-sagemaker-xgboost-lab}"
PERMISSIONS_BOUNDARY_ARN="${PERMISSIONS_BOUNDARY_ARN:-}"
account_id="$(aws sts get-caller-identity --query Account --output text)"
bucket_name="sagemaker-xgboost-lab-${account_id}"
mlflow_artifact_uri="s3://${bucket_name}/sagemaker-xgboost-lab/mlflow/"

stack_output() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" \
    --output text
}

find_mlflow_app_arn() {
  aws sagemaker list-mlflow-apps \
    --region "$AWS_REGION" \
    --query "Summaries[?Name=='${MLFLOW_APP_NAME}' && Status!='Deleted'].Arn | [0]" \
    --output text
}

mlflow_status() {
  aws sagemaker describe-mlflow-app \
    --arn "$1" \
    --region "$AWS_REGION" \
    --query Status \
    --output text
}

wait_for_app_deletion() {
  local deadline=$((SECONDS + 600))
  local arn
  while ((SECONDS < deadline)); do
    arn="$(find_mlflow_app_arn)"
    if [[ "$arn" == "None" || -z "$arn" ]]; then
      return
    fi
    printf 'MLflow App status: %s\n' "$(mlflow_status "$arn" 2>/dev/null || true)"
    sleep 10
  done
  printf 'Timed out waiting for the MLflow App to be deleted.\n' >&2
  exit 1
}

create_mlflow_app() {
  local role_arn="$1"
  aws sagemaker create-mlflow-app \
    --name "$MLFLOW_APP_NAME" \
    --artifact-store-uri "$mlflow_artifact_uri" \
    --role-arn "$role_arn" \
    --model-registration-mode AutoModelRegistrationEnabled \
    --tags Key=Project,Value=sagemaker-xgboost-lab \
    --region "$AWS_REGION" \
    --query Arn \
    --output text
}

wait_for_app_ready() {
  local arn="$1"
  local deadline=$((SECONDS + 600))
  local previous=""
  local status
  while ((SECONDS < deadline)); do
    status="$(mlflow_status "$arn")"
    if [[ "$status" != "$previous" ]]; then
      printf 'MLflow App status: %s\n' "$status"
      previous="$status"
    fi
    case "$status" in
      Created | Updated)
        return
        ;;
      CreateFailed | UpdateFailed | DeleteFailed)
        printf 'MLflow App entered a failed state: %s\n' "$status" >&2
        exit 1
        ;;
    esac
    sleep 10
  done
  printf 'Timed out waiting for the MLflow App. Last status: %s\n' "$status" >&2
  exit 1
}

stack_status="$(
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query 'Stacks[0].StackStatus' \
    --output text 2>/dev/null || true
)"
if [[ "$stack_status" == "ROLLBACK_COMPLETE" ]]; then
  printf 'Removing failed stack: %s\n' "$STACK_NAME"
  aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$AWS_REGION"
  aws cloudformation wait stack-delete-complete \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION"
  stack_status=""
fi

if [[ -n "$stack_status" ]]; then
  create_bucket="$(
    aws cloudformation describe-stacks \
      --stack-name "$STACK_NAME" \
      --region "$AWS_REGION" \
      --query "Stacks[0].Parameters[?ParameterKey=='CreateArtifactBucket'].ParameterValue | [0]" \
      --output text
  )"
  if [[ "$create_bucket" == "false" ]] &&
    ! aws s3api head-bucket --bucket "$bucket_name" >/dev/null 2>&1; then
    create_bucket=true
  fi
elif aws s3api head-bucket --bucket "$bucket_name" >/dev/null 2>&1; then
  create_bucket=false
  printf 'Reusing existing artifact bucket: %s\n' "$bucket_name"
else
  create_bucket=true
fi

printf '\n[1/2] Deploy S3 bucket and IAM role\n'
if ! aws cloudformation deploy \
  --template-file infra/main.yaml \
  --stack-name "$STACK_NAME" \
  --parameter-overrides \
    "BucketName=${bucket_name}" \
    "CreateArtifactBucket=${create_bucket}" \
    "PermissionsBoundaryArn=${PERMISSIONS_BOUNDARY_ARN}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$AWS_REGION"; then
  aws cloudformation describe-stack-events \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query "StackEvents[?contains(ResourceStatus, 'FAILED')].[Timestamp,LogicalResourceId,ResourceStatusReason]" \
    --output table >&2 || true
  exit 1
fi

execution_role_arn="$(stack_output SageMakerExecutionRoleArn)"
mlflow_role_arn="$(stack_output MlflowAppRoleArn)"

printf '\n[2/2] Prepare MLflow App\n'
mlflow_app_arn="$(find_mlflow_app_arn)"
if [[ "$mlflow_app_arn" == "None" || -z "$mlflow_app_arn" ]]; then
  mlflow_app_arn="$(create_mlflow_app "$mlflow_role_arn")"
else
  status="$(mlflow_status "$mlflow_app_arn")"
  current_role_arn="$(
    aws sagemaker describe-mlflow-app \
      --arn "$mlflow_app_arn" \
      --region "$AWS_REGION" \
      --query RoleArn \
      --output text
  )"
  current_artifact_uri="$(
    aws sagemaker describe-mlflow-app \
      --arn "$mlflow_app_arn" \
      --region "$AWS_REGION" \
      --query ArtifactStoreUri \
      --output text
  )"
  if [[ "$current_role_arn" != "$mlflow_role_arn" ||
        "$current_artifact_uri" != "$mlflow_artifact_uri" ]]; then
    printf 'Replacing MLflow App with the current role and artifact store.\n'
    aws sagemaker delete-mlflow-app \
      --arn "$mlflow_app_arn" \
      --region "$AWS_REGION" \
      >/dev/null
    wait_for_app_deletion
    mlflow_app_arn="$(create_mlflow_app "$mlflow_role_arn")"
  else
    case "$status" in
      CreateFailed | UpdateFailed | DeleteFailed)
        aws sagemaker delete-mlflow-app \
          --arn "$mlflow_app_arn" \
          --region "$AWS_REGION" \
          >/dev/null
        wait_for_app_deletion
        mlflow_app_arn="$(create_mlflow_app "$mlflow_role_arn")"
        ;;
      Deleting)
        wait_for_app_deletion
        mlflow_app_arn="$(create_mlflow_app "$mlflow_role_arn")"
        ;;
    esac
  fi
fi
wait_for_app_ready "$mlflow_app_arn"

model_registration_mode="$(
  aws sagemaker describe-mlflow-app \
    --arn "$mlflow_app_arn" \
    --region "$AWS_REGION" \
    --query ModelRegistrationMode \
    --output text
)"
if [[ "$model_registration_mode" != "AutoModelRegistrationEnabled" ]]; then
  printf 'Enabling MLflow model registration sync.\n'
  aws sagemaker update-mlflow-app \
    --arn "$mlflow_app_arn" \
    --model-registration-mode AutoModelRegistrationEnabled \
    --region "$AWS_REGION" \
    >/dev/null
  wait_for_app_ready "$mlflow_app_arn"
fi

mlflow_version="$(
  aws sagemaker describe-mlflow-app \
    --arn "$mlflow_app_arn" \
    --region "$AWS_REGION" \
    --query MlflowVersion \
    --output text
)"
if [[ "$mlflow_version" != 3.10.* ]]; then
  printf 'Unsupported MLflow App version: %s (expected 3.10.x)\n' "$mlflow_version" >&2
  exit 1
fi

printf '\nInfrastructure ready.\n'
printf 'Artifact bucket: %s\n' "$bucket_name"
printf 'Execution role: %s\n' "$execution_role_arn"
printf 'MLflow App: %s\n' "$mlflow_app_arn"
printf 'Next: ./scripts/run-training.sh\n'
