English | [한국어](README.ko.md)

# Infrastructure deployment

Run the following command once before Training.

```bash
./scripts/bootstrap-infra.sh
```

The script performs only two steps.

## 1. S3 bucket and IAM roles

It deploys `infra/main.yaml` as the `sagemaker-xgboost-lab` CloudFormation stack.

| Resource | Purpose |
|---|---|
| S3 artifact bucket | Stores Training input, models, and MLflow artifacts |
| SageMaker Training role | Lets the Training Job read input and container images and store models, logs, and metrics |
| MLflow App role | Used by managed MLflow to read and write its dedicated S3 prefix |

New buckets have Block Public Access and a policy that denies HTTP access. Reused buckets keep their existing policy; this stack does not manage it. The Training role's S3 permissions are limited to the project prefix, and the role also includes the permissions needed to pull ECR images, write Training logs and metrics, and call the MLflow App API for the autolog example. It also includes network interface permissions to pass the SageMaker SDK v3 role pre-check, although this sample itself does not use a VPC configuration.

New deployments use `sagemaker-xgboost-lab-<account-id>-<region>`. Existing stacks retain the bucket named in their outputs. Bootstrap checks the bucket owner and Region before reusing it. CloudFormation generates unique IAM role names; launchers use the role ARNs from stack outputs. The S3 artifact prefix is fixed to `sagemaker-xgboost-lab` in both code and policies.

If your organization requires an IAM permissions boundary, you can pass it as an environment variable.

```bash
PERMISSIONS_BOUNDARY_ARN='<boundary-arn>' ./scripts/bootstrap-infra.sh
```

## 2. MLflow App

It creates the `sagemaker-xgboost-lab` MLflow App using the MLflow role and S3 bucket from the CloudFormation outputs.

The MLflow App is created asynchronously by the SageMaker API. The script checks the status every 10 seconds and waits up to 10 minutes until it becomes `Created` or `Updated`.

```text
[1/2] Deploy S3 bucket and IAM role
[2/2] Prepare MLflow App
MLflow App status: Creating
MLflow App status: Created
```

If an App already exists, bootstrap compares its role and artifact store with the stack outputs. A mismatch or failed state stops bootstrap without deleting the App. After reviewing the printed values, use `./scripts/bootstrap-infra.sh --replace-mlflow-app` only if you intend to delete and recreate that App. This is not an in-place migration of the existing App's tracking history.

## Deployment output

When it completes, the following values are printed.

```text
Infrastructure ready.
Artifact bucket: sagemaker-xgboost-lab-<account-id>-<region>
Execution role: <training-role-arn>
MLflow App: <mlflow-app-arn>
Next: ./scripts/run-training.sh
```

`run-training.sh` looks up these values automatically, so you do not need to `export` them yourself.

## Required execution permissions

The identity that runs bootstrap needs permissions to create the following resources.

- CloudFormation stack
- S3 bucket and bucket policy
- IAM roles and inline policies
- SageMaker MLflow App

The identity that runs Training needs permissions for S3 upload/download, SageMaker Training Job
creation, `iam:PassRole` to pass the execution role, and MLflow App access. To run Automatic
Model Tuning, it also needs permissions to create and describe SageMaker Hyperparameter Tuning
Jobs.

### Batch Transform execution permissions

The user or CI identity that runs `run-batch-inference.sh` is not the SageMaker execution
role. Grant it the following minimum permissions separately.

| Permission | Scope |
|---|---|
| `cloudformation:DescribeStacks` | `sagemaker-xgboost-lab` stack |
| `sagemaker:ListModelPackageGroups`, `sagemaker:ListModelPackages`, `sagemaker:DescribeModelPackage` | Look up registered Model Packages |
| `sagemaker:CreateModel`, `sagemaker:DeleteModel` | `xgboost-lab-batch-*` temporary models |
| `sagemaker:CreateTransformJob`, `sagemaker:DescribeTransformJob` | `xgboost-lab-batch-*` Transform Jobs |
| `iam:PassRole` | Only the `SageMakerExecutionRoleArn` from the CloudFormation outputs |
| `s3:PutObject`, `s3:GetObject` | `sagemaker-xgboost-lab/credit-card-default/batch/*` prefix |

Always restrict `iam:PassRole` to this sample's SageMaker execution role. The permissions for
Batch Transform to read S3 input and write predictions are already included in that execution
role.

## Running again

You can run the same command again. CloudFormation updates only the parts that changed. If the existing MLflow App's IAM role and artifact store match the current infrastructure, the App is reused; otherwise, bootstrap stops and requires `--replace-mlflow-app` to recreate it.

If the stack is in `ROLLBACK_COMPLETE`, bootstrap deletes the failed stack and redeploys it. If the deployment fails, the last CloudFormation failure event is shown in the terminal.

## Updating an earlier deployment

Review a CloudFormation change set before rerunning bootstrap against an older stack. Removing fixed
role names can replace the roles, and adding the bucket policy condition can remove a policy previously
managed on a reused bucket. Check the current bucket policy and any custom artifact prefix before applying.

From `sm-xgboost-lab`, create a change set without executing it:

```bash
BUCKET_NAME="$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME:-sagemaker-xgboost-lab}" \
  --region "${AWS_REGION:-ap-northeast-2}" \
  --query "Stacks[0].Outputs[?OutputKey=='ArtifactBucketName'].OutputValue | [0]" --output text)"
CREATE_BUCKET="$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME:-sagemaker-xgboost-lab}" \
  --region "${AWS_REGION:-ap-northeast-2}" \
  --query "Stacks[0].Parameters[?ParameterKey=='CreateArtifactBucket'].ParameterValue | [0]" --output text)"
aws cloudformation deploy --template-file infra/main.yaml \
  --stack-name "${STACK_NAME:-sagemaker-xgboost-lab}" \
  --region "${AWS_REGION:-ap-northeast-2}" \
  --parameter-overrides "BucketName=${BUCKET_NAME}" "CreateArtifactBucket=${CREATE_BUCKET}" \
  --capabilities CAPABILITY_NAMED_IAM --no-execute-changeset
```

Inspect the change set in CloudFormation before executing it. Keep the existing bucket name and creation
setting. Unspecified existing parameters, including the permissions boundary, retain their previous values.
If the execution role changes, update dependent policies and rerun Pipeline setup and registration.
If the MLflow role changes, plan the App replacement explicitly; ordinary bootstrap will stop on the mismatch.

If the stack still owns a bucket that was manually deleted, bootstrap stops rather than silently switching
buckets. Restore that bucket or deliberately clean up the obsolete stack before starting a fresh deployment.
