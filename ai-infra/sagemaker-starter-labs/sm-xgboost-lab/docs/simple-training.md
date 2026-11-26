English | [한국어](simple-training.ko.md)

# Simple built-in XGBoost training

`train_simple.py` is the first training example to read when you are adopting SageMaker for the first time.
It leaves out MLflow, Model Registry, model download, and the Batch Transform integration entirely, and performs only the
following three steps.

```text
Prepare the public dataset and upload it to S3
  -> SageMaker built-in XGBoost Training Job
  -> Print validation AUC, Training Job name, and model S3 URI
```

## Run

Once the CloudFormation stack has been deployed, you can run this regardless of the MLflow App
status.

```bash
./scripts/run-simple-training.sh
```

The default instance is `ml.c6i.xlarge`.

```bash
./scripts/run-simple-training.sh --instance-type ml.m6i.xlarge
```

## How to read the code

Read `run_training()` in [`train_simple.py`](../src/sagemaker_xgboost_lab/train_simple.py)
from top to bottom.

1. `prepare_training_data()` converts the UCI credit card default data into SageMaker CSV format
   and uploads it from local to S3.
2. `image_uris.retrieve()` selects the AWS built-in image that matches the Region and XGBoost
   version.
3. `ModelTrainer` defines the Training Job by combining the Training image, execution role, instance, hyperparameters, and model
   output S3 path.
4. `trainer.train()` starts the Job, passing the `train` and `validation` S3 channels.
5. When the Job finishes, the `validation:auc` computed by SageMaker and the model artifact location are
   printed to the terminal.

`train` and `validation` are channel names that distinguish the S3 data. Inside the container, they are
mounted at `/opt/ml/input/data/train/` and `/opt/ml/input/data/validation/`, respectively.
For the detailed CSV schema, see the [Data preparation guide](data-preparation.md).

## Fixed hyperparameters

This example uses only a single, easy-to-read XGBoost configuration.

| Hyperparameter | Value |
|---|---:|
| `objective` | `binary:logistic` |
| `eval_metric` | `auc` |
| `tree_method` | `hist` |
| `num_round` | `300` |
| `max_depth` | `5` |
| `eta` | `0.08` |
| `subsample` | `0.9` |
| `colsample_bytree` | `0.9` |

These values are not production recommendations for this public dataset. They are a starting point for understanding the
structure of a single built-in Training Job.

## What is not included

This example does not create MLflow runs, dataset lineage, a Logged Model, Model Registry registration, or Batch
Transform. It is therefore a good fit when you want to check the SageMaker Training Job and the S3 model artifact
first.

When you need to compare experiments, move on to `train.py`; for automatic per-iteration logging, `train_autolog.py`;
and to search multiple hyperparameter combinations, `train_tunner.py`.
