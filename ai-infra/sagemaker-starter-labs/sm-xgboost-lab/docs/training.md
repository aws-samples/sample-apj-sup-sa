English | [한국어](training.ko.md)

# Training execution flow

The following single command starts the entire Python flow.

```bash
./scripts/run-training.sh
```

`run-training.sh` reads the S3 bucket and Training role from the CloudFormation outputs and finds the ready MLflow App ARN. It passes these values to the Python process and then runs the `sagemaker-xgboost-lab` CLI.

## 1. Verify the MLflow App connection

Before any SageMaker Training cost is incurred, `train.py` sets the MLflow App as the tracking URI
and prepares the experiment. If the connection fails, it exits before creating a
Training Job. If the infrastructure configuration does not match, prepare the App again with the following command.

```bash
./scripts/bootstrap-infra.sh
```

## 2. Prepare the public dataset and upload it to S3

`data_prep.py` downloads the UCI Default of Credit Card Clients dataset. It validates the schema of the 23
features and the next-month default label, and splits the data into 80% train and 20% validation.
Because sampling is stratified by class, the default rate in both splits stays at about 22.1%, and
using the same random seed produces the same split.

CSV input for SageMaker built-in XGBoost must have the label in the first column and no header. The Python code creates `train.csv` and `validation.csv` in this format and uploads them to a per-run S3 prefix.

```text
s3://<bucket>/sagemaker-xgboost-lab/credit-card-default/runs/<run-id>/data/
```

The SageMaker Training Job reads these S3 URIs as the `train` and `validation` channels.

The dataset schema, split strategy, and the difference between the Training and Batch Transform CSVs are described in the [Data preparation guide](data-preparation.md).

## 3. Run two Training Jobs

`train.py` contains two easy-to-compare configurations in code.

| Run | Difference |
|---|---|
| `default` | A model whose depth and boosting rounds are intentionally limited so that the difference is easy to see |
| `tuned` | Deeper trees, a smaller learning rate, and more boosting rounds |

For each configuration, SDK v3 `ModelTrainer` creates a separate SageMaker Training Job. Because `wait=True` and `logs=True`, it waits until the Job finishes and shows the Training logs in the terminal.

`default` does not represent typical XGBoost defaults or recommended settings. It is a demo baseline with intentionally limited training
so that you can see the difference before and after improvement.

CPU instances use `tree_method=hist`. If you choose `ml.g4dn.*` or `ml.g5.*`, it switches to `tree_method=gpu_hist`.

## 4. Evaluation and MLflow logging

When Training finishes, the `validation:auc` recorded by the XGBoost container is read from the completed Training
Job and logged to MLflow as `validation-auc`. The `model.tar.gz` created by SageMaker is
opened as a local XGBoost `Booster` to create the MLflow Logged Model.

Each candidate is a separate run in MLflow.

```text
default-<run-id>
tuned-<run-id>
```

Each run records the following information.

- XGBoost hyperparameters
- SageMaker image URI and instance type
- Validation AUC from the SageMaker Training Job
- SageMaker Training Job name
- Model artifact S3 URI
- Dataset summary JSON

Finally, the terminal prints the results of both runs and the run with the highest validation AUC. This selection is a simple comparison for learning purposes and does not imply production model approval or deployment.

For a flow in which SageMaker automatically searches multiple hyperparameter combinations,
see the [Automatic Model Tuning guide](automatic-model-tuning.md).
