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

The bucket has Block Public Access and a policy that denies HTTP access. The Training role's S3 permissions are limited to the project prefix, and the role also includes the permissions needed to pull ECR images, write Training logs and metrics, and call the MLflow App API for the autolog example. It also includes network interface permissions to pass the SageMaker SDK v3 role pre-check, although this sample itself does not use a VPC configuration.

The bucket name is set to `sagemaker-xgboost-lab-<account-id>` using the current account ID. If the same bucket created by a previous version is found, the new stack reuses it instead of creating it again.

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

If an App is already in place, the script compares the App's IAM role and artifact store with the current CloudFormation outputs. An App that references a deleted old role, uses a different S3 path, or is in a failed state is deleted and recreated with the current configuration.

## Deployment output

When it completes, the following values are printed.

```text
Infrastructure ready.
Artifact bucket: sagemaker-xgboost-lab-<account-id>
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

You can run the same command again. CloudFormation updates only the parts that changed. If the existing MLflow App's IAM role and artifact store match the current infrastructure, the App is reused; otherwise, it is recreated.

If the stack is in `ROLLBACK_COMPLETE`, bootstrap deletes the failed stack and redeploys it. If the deployment fails, the last CloudFormation failure event is shown in the terminal.
