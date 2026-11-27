English | [한국어](troubleshooting.ko.md)

# Troubleshooting

## Bootstrap reports `Unsupported MLflow App version`

Older bootstrap scripts restricted the MLflow App to `3.10.x`. The current script reports the
App version, and the local and autolog training environments pin the MLflow client to `3.16.0`.
After updating the code, sync dependencies and rerun bootstrap:

```bash
uv sync --frozen --extra dev
./scripts/bootstrap-infra.sh
```

This version error does not require deleting or recreating the App. If using the Pipeline,
run `uv sync --frozen --extra dev` and `./scripts/setup.sh` in `sm-pipeline-quick` as well.
Reporting the server version is not a connectivity check; investigate connection errors separately.

## AWS credentials not found

Check that the following command returns your account information.

```bash
aws sts get-caller-identity
```

## `AccessDenied`

Check that the current identity has CloudFormation, S3, and SageMaker permissions. Training requires
permission to create SageMaker Training Jobs, and AMT additionally requires permission to create Hyperparameter Tuning Jobs.
In both cases, `iam:PassRole` is required for the Training role created by
CloudFormation.

## MLflow App not found

Deploy the infrastructure first.

```bash
./scripts/bootstrap-infra.sh
```

Check the current status as follows.

```bash
aws sagemaker list-mlflow-apps \
  --region ap-northeast-2 \
  --query "Summaries[?Name=='sagemaker-xgboost-lab'].[Name,Status,MlflowVersion,Arn]" \
  --output table
```

## HTTP 502 from the MLflow endpoint

Even if the MLflow App status is `Created`, the endpoint can return `502` if the App references a deleted old IAM role. Running the following command again compares the App's role and artifact store with the current CloudFormation outputs and, if they differ, deletes the App and recreates it with the correct configuration.

```bash
./scripts/bootstrap-infra.sh
./scripts/run-training.sh --instance-type ml.g5.xlarge
```

The Training code also checks the MLflow connection before creating a billable Training Job, and exits with a short message if it cannot connect.

## MLflow artifact upload `AccessDenied`

The autolog Training Job uploads `data-summary.json`, the model, and feature importance directly to the MLflow artifact
prefix. Therefore, not only the MLflow App role but also the **SageMaker Training
role** needs `s3:PutObject` permission on `${ArtifactPrefix}/mlflow/*`.

If you see an `s3:PutObject` error for the `sagemaker-xgboost-lab-training` role like this,
redeploy the infrastructure.

```bash
./scripts/bootstrap-infra.sh
./scripts/run-autolog-training.sh
```

## GPU instance quota error

If your account has no `ml.g4dn.xlarge` or `ml.g5.xlarge` Training quota, Job creation is rejected. Check the corresponding SageMaker Training instance quota in Service Quotas, or run with the default `ml.c6i.xlarge`.

Batch Transform uses a different quota from Training. This sample is fixed to a single instance type, `ml.c5.xlarge`,
so check in Service Quotas that **for transform job usage** for this instance is `1` or
higher.

```bash
./scripts/run-training.sh
```

## Automatic Model Tuning does not start

AMT's `max-parallel-jobs` cannot exceed the concurrent Training quota for that instance.
Run again with the default value `1`, or check the Training quota in Service Quotas.

```bash
./scripts/run-tuning.sh --max-jobs 4 --max-parallel-jobs 1
```

AMT can run up to `max-jobs` Training Jobs. It takes more time and
cost than regular Training, and even if you stop a Tuning Job, you are still charged for the Training that has already run.

## AMT interprets CSV as LIBSVM

If the Training Job's `InputDataConfig` does not have `ContentType=text/csv`, built-in XGBoost
interprets the CSV in its default LIBSVM format and fails. This project's `train_tunner.py` uses `Channel` instead of
SDK v3's simple `InputData` conversion to specify the content type explicitly.

## Batch Transform cannot find an Approved model

Even after you register a model in the MLflow UI, its deployment status in SageMaker Model Registry may initially appear as **Pending Approval**. In SageMaker Studio, go to **JumpStart / Models → My models → Logged**, open the model group and then the version, and change the **Overview → Deploy** status to **Approved**.

`run-batch-inference.sh` automatically finds the Model Package Group created by MLflow. If there are multiple groups with the same prefix, set `MODEL_PACKAGE_GROUP_NAME` to the exact name shown in Studio. The script uses only versions that support `ml.c5.xlarge` among the latest `Approved` versions in this group. If there are only **Pending Approval**, **Rejected**, or incompatible versions, it stops before any inference cost is incurred.

```bash
./scripts/run-batch-inference.sh
```

## UCI dataset download fails

Check that your local machine can access the UCI repository. The dataset is downloaded not by SageMaker but by the local Python process that ran the command.

## Training Job fails

Check the last error in the SageMaker log printed to the terminal. You can also view the logs and failure reason under the same Job name on the Training jobs page of the SageMaker console.

## `RoleValidationError`

Before creating a Training Job, `ModelTrainer` checks the execution role's S3, ECR, CloudWatch, and EC2 network permissions. If the role created by this repository has an outdated configuration, redeploy the infrastructure to update the policy.

```bash
./scripts/bootstrap-infra.sh
./scripts/run-training.sh --instance-type ml.g5.xlarge
```

Because this check runs before the Job is created, no Training instance cost is incurred if the error occurs at this stage.

## Run exists in MLflow but has no metrics

Check that the SageMaker Training Job completed and that `validation:auc` was recorded. The MLflow metric is stored as `validation-auc` by reading this value from the completed Job.
