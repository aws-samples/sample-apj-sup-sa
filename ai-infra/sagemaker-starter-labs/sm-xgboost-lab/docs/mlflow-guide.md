English | [한국어](mlflow-guide.ko.md)

# Guide to Reviewing Results in MLflow

This document walks step by step through opening managed MLflow after Training finishes, finding the `default` and `tuned` runs, and comparing their results.

In this example, MLflow is not a tool that trains or deploys models directly. It is an experiment tracking tool that lets you see in one place which settings were used for training, what the results were, and which SageMaker Training Job and model artifact each result is linked to.

## Structure to understand first

The MLflow UI is organized into the following three levels.

```text
MLflow App
  -> Experiment
      -> Run
```

| Level | Value in this example | Meaning |
|---|---|---|
| MLflow App | `sagemaker-xgboost-lab` | Managed App that provides the MLflow UI and metadata |
| Experiment | `sagemaker-xgboost-lab` | Space that groups Training results with the same purpose |
| Run | `default-<run-id>`, `tuned-<run-id>` | Result of a run with one hyperparameter configuration |
| AMT Run | `amt-<run-id>` | Best trial selected by Automatic Model Tuning |

Running `./scripts/run-training.sh` once creates one `default` run and one `tuned` run with the same `<run-id>`. When comparing the two configurations, select runs whose `<run-id>` suffix matches.

`./scripts/run-tuning.sh` runs multiple SageMaker Training Jobs and then records the best trial as
the `amt-<run-id>` run. View all trials in the SageMaker Hyperparameter tuning jobs console; a
summary is also available in the `tuning/trials.json` artifact in MLflow.

For example, the following two runs form a pair.

```text
default-20260803-055035-9139f1
tuned-20260803-055035-9139f1
```

## 1. Open the MLflow App

1. In the AWS Console, select the `ap-northeast-2` Region.
2. Go to **Amazon SageMaker AI → SageMaker Studio**. If you do not have Studio, complete **Create SageMaker Studio**.
3. In the created Studio, choose **Open Studio**.
4. In the left navigation of Studio, choose the **MLflow** icon.
5. Select the `sagemaker-xgboost-lab` App and choose **Open MLflow**.
6. When MLflow opens, select **Model training** in the upper left.

The MLflow 3 UI may initially open in **GenAI** mode. This example records conventional model Training results, so be sure to switch to **Model training**.

Select `sagemaker-xgboost-lab` from **Recent Experiments** on the Home screen, or select the same name under **Experiments** on the left.

## 2. Read the Runs list

When you open the Experiment, the **Runs** screen lists the results of all runs so far in chronological order.

First check the following items.

| Screen item | What to check |
|---|---|
| Run Name | Confirm they are `default` and `tuned` with the same `<run-id>` |
| Status icon | Green means completed, red means failed |
| Created | When the run started |
| Duration | Total time taken for Training and evaluation |
| Source | `sagemaker-xgboost-lab` in this example |

If you compare runs with different `<run-id>` suffixes, you will be mixing executions from different points in time. When viewing the latest results, first confirm that the topmost `default` and `tuned` runs have the same suffix.

Failed runs from previous executions may remain. Failed runs are useful for tracing the cause, but select only completed runs for the final performance comparison.

## 3. Inspect an individual run

Selecting a Run Name opens that run's **Overview**.

Start by looking at the following three areas in order.

### Metrics

Metrics are the results the model produced on the validation dataset.

| Metric | Meaning | How to read it |
|---|---|---|
| `validation-auc` | Overall ranking performance in separating positives from negatives | Closer to `1.0` is better; `0.5` is the level of random guessing |

`validation-auc` is the SageMaker `validation:auc` value computed by the XGBoost container during Training, read from the completed Training Job and recorded in MLflow. Because it is the same value, the metric result does not differ between SageMaker and MLflow. Autolog runs use the same name, so you can select them together on a single Compare screen.

### Parameters

Parameters show which settings the model was trained with.

| Parameter | Meaning |
|---|---|
| `xgboost.objective` | The problem being solved and the prediction output format. `binary:logistic` outputs a binary classification probability |
| `xgboost.eval_metric` | Metric to monitor during Training. This example uses `auc` |
| `xgboost.tree_method` | How trees are built. GPU instances use `gpu_hist` |
| `xgboost.seed` | Random seed for reproducing results under the same conditions |
| `xgboost.num_round` | Number of trees added sequentially |
| `xgboost.max_depth` | Maximum depth to which each tree can grow |
| `xgboost.eta` | Learning rate that controls the influence of each tree |
| `xgboost.subsample` | Fraction of rows each tree uses for training |
| `xgboost.colsample_bytree` | Fraction of features each tree uses |
| `sagemaker.instance_type` | SageMaker instance used for Training |
| `sagemaker.image_uri` | XGBoost container image and version used |
| `dataset.uci_id` | Public dataset ID used in this example |
| `dataset.name` | UCI Default of Credit Card Clients |
| `dataset.positive_label` | `1 = default payment next month` |

A smaller `eta` means less is learned per step, so more `num_round` is usually needed. In this example, `tuned` uses `eta=0.08` and `num_round=250`, while `default` uses `eta=0.1` and `num_round=10`.

`0.9` is the value of `subsample` and `colsample_bytree`, not `eta`.

### Tags and Artifacts

The Tags at the bottom of the screen link the MLflow run to SageMaker resources.

| Tag | Meaning |
|---|---|
| `candidate` | `default` or `tuned` |
| `run_id` | Project run ID that identifies a single script execution |
| `sagemaker.instance_type` | Training instance used |
| `sagemaker.training_job_name` | Job name to use when looking up logs and metrics in the SageMaker console |
| `sagemaker.model_s3_uri` | S3 URI where the trained `model.tar.gz` is stored |
| `mlflow_run_id` | ID that identifies the run within MLflow |

**Datasets** shows the schema, digest, and S3 source of the actual Training and validation
DataFrames. The `data-summary.json` file in the **Artifacts** tab contains the dataset name,
feature order, target meaning, row and class counts per split, and random seed.

`model` in the **Artifacts** tab is the MLflow Logged Model. Once you have chosen the run to use after comparison, open `model` and choose **Register model** to register it under the name `sagemaker-xgboost-lab`. This also creates the same version in SageMaker Model Registry.

The original model created by SageMaker is at `sagemaker.model_s3_uri`, and the inference specification included in the Logged Model links this URI to the XGBoost image.

## 4. Compare default and tuned

Return to the Runs screen and compare in the following order.

1. Find the `default` and `tuned` runs with the same `<run-id>` suffix.
2. Select the checkboxes for both rows.
3. Choose **Compare** at the top.
4. Turn on **Show diff only** under **Parameters** and **Metrics** to quickly see only the values that differ.

### Reading the visualizations

The **Parallel Coordinates Plot** at the top of the Compare screen connects parameters and metrics with lines.

Choose the parameters to compare under **Parameters** on the left, and the result metric under **Metrics**. When there are only two runs, as in this example, use it to see which settings led to which results rather than to analyze overall trends.

As runs accumulate, you can use the Scatter Plot to view the relationship between `eta` and ROC-AUC, or to find good regions among many parameter combinations.

### Interpreting results in the table

Scrolling down lets you compare parameters and metrics column by column.

| Item | tuned | default | Interpretation |
|---|---:|---:|---|
| `xgboost.max_depth` | 5 | 1 | tuned can learn more complex relationships |
| `xgboost.num_round` | 250 | 10 | tuned sequentially trains more trees |
| `xgboost.eta` | 0.08 | 0.1 | tuned learns in smaller steps |
| `validation-auc` | Check run result | Check run result | Higher means better ranking of defaulting vs. non-defaulting customers |

A local sanity check on the same split showed a baseline of about `0.70` and tuned of about
`0.76`, but SageMaker results can vary with the container version and execution environment.
Compare based on the actual MLflow values. Even if tuned has a higher AUC, it requires more trees
and time, so consider the size of the improvement together with the added complexity.

## 5. What to check when actually selecting a model

Rather than just looking for the highest number in MLflow, check in the following order.

1. Use `run_id`, dataset ID, and seed to confirm that the runs being compared used the same dataset and split.
2. Confirm that validation AUC actually improved.
3. Check the Training log or autolog learning curve for signs of overfitting, where only the train metric is high while the validation metric drops.
4. Review the instance type, Duration, and number of trees to assess the additional cost and complexity.
5. Check the selected run's Training Job name and model S3 URI in Tags.

The `default` in this example is a deliberately small demo configuration so that the improvement is easy to see. Therefore, do not interpret the result that tuned is better as a general conclusion about XGBoost. With real data, repeat multiple experiments and make the final decision using a separate test dataset and business metrics.

AMT runs also use the same validation set to select the best trial. Therefore, to judge final
performance, you need a separate test or out-of-time dataset that AMT has not seen.

## 6. The order in which the Python code logs to MLflow

The values on the screen are logged by `train.py` in the following order.

First, select the managed MLflow App and experiment.

```python
mlflow.set_tracking_uri(mlflow_app_arn)
mlflow.set_experiment("sagemaker-xgboost-lab")
```

Open a separate run for each candidate. This name becomes the Run Name on the Runs screen.

```python
with mlflow.start_run(run_name=f"{candidate}-{run_id}"):
    ...
```

Before Training, record the settings needed for reproducibility in Parameters and Tags.

```python
mlflow.log_params(hyperparameters)
mlflow.set_tags(
    {
        "candidate": candidate,
        "run_id": run_id,
        "sagemaker.instance_type": instance_type,
    }
)
```

After Training finishes, record the validation AUC computed by SageMaker.

```python
mlflow.log_metric("validation-auc", validation_auc)
```

Finally, link the SageMaker Training Job and model location in Tags, and save the dataset summary as an Artifact.

```python
mlflow.set_tags(
    {
        "sagemaker.training_job_name": training_job_name,
        "sagemaker.model_s3_uri": model_s3_uri,
    }
)
mlflow.log_artifact("data-summary.json", artifact_path="data")
```

| Python call | MLflow screen |
|---|---|
| `mlflow.start_run()` | Run Name and run status |
| `mlflow.log_params()` | Parameters |
| `mlflow.log_metrics()` | Metrics |
| `mlflow.set_tags()` | Tags |
| `mlflow.log_artifact()` | Artifacts |

You can find the actual implementation in `run_experiment()` in [`train.py`](../src/sagemaker_xgboost_lab/train.py).

