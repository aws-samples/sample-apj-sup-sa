"""Train XGBoost inside SageMaker and let MLflow record the run automatically."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd
import xgboost as xgb


def load_channel(channel_dir: str, feature_names: list[str]) -> xgb.DMatrix:
    """Load the single label-first CSV file mounted for a SageMaker channel."""

    csv_path = next(Path(channel_dir).glob("*.csv"))
    rows = np.loadtxt(csv_path, delimiter=",", dtype=np.float32)
    features = pd.DataFrame(rows[:, 1:], columns=feature_names)
    return xgb.DMatrix(features, label=rows[:, 0])


def load_feature_names(metadata_dir: str) -> tuple[list[str], Path]:
    """Read the feature order produced by local data preparation."""

    summary_path = next(Path(metadata_dir).glob("*.json"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return [str(name) for name in summary["feature_names"]], summary_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max_depth", type=int, default=5)
    parser.add_argument("--eta", type=float, default=0.08)
    parser.add_argument("--subsample", type=float, default=0.9)
    parser.add_argument("--colsample_bytree", type=float, default=0.9)
    parser.add_argument("--num_boost_round", type=int, default=250)
    parser.add_argument("--early_stopping_rounds", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    mlflow.set_experiment(os.environ["MLFLOW_EXPERIMENT_NAME"])
    # Enable autolog before creating DMatrix objects so MLflow can capture
    # the input example and infer the model signature.
    mlflow.xgboost.autolog(
        log_input_examples=True,
        log_model_signatures=True,
        log_datasets=True,
    )
    feature_names, summary_path = load_feature_names(os.environ["SM_CHANNEL_METADATA"])
    train = load_channel(os.environ["SM_CHANNEL_TRAIN"], feature_names)
    validation = load_channel(os.environ["SM_CHANNEL_VALIDATION"], feature_names)

    parameters = {
        "objective": "binary:logistic",
        "eval_metric": "auc",
        "tree_method": "hist",
        "max_depth": args.max_depth,
        "eta": args.eta,
        "subsample": args.subsample,
        "colsample_bytree": args.colsample_bytree,
        "seed": args.seed,
    }
    if int(os.environ["SM_NUM_GPUS"]) > 0:
        parameters["device"] = "cuda"

    # Autolog intercepts xgb.train() and records parameters, per-round metrics,
    # feature importance, the model, input example, and model signature.
    with mlflow.start_run(run_name=os.environ["MLFLOW_RUN_NAME"]):
        mlflow.set_tags(
            {
                "logging": "mlflow-autolog",
                "sagemaker.training_job_name": os.environ["TRAINING_JOB_NAME"],
                "sagemaker.instance_type": os.environ["SAGEMAKER_INSTANCE_TYPE"],
                "sagemaker.image_uri": os.environ["SAGEMAKER_IMAGE_URI"],
                "dataset.uci_id": os.environ["DATASET_ID"],
                "dataset.name": os.environ["DATASET_NAME"],
                "dataset.positive_label": "1 = default payment next month",
                "dataset.train_s3_uri": os.environ["TRAIN_S3_URI"],
                "dataset.validation_s3_uri": os.environ["VALIDATION_S3_URI"],
                "dataset.summary_s3_uri": os.environ["DATA_SUMMARY_S3_URI"],
            }
        )
        mlflow.log_artifact(str(summary_path), artifact_path="data")
        evaluation_results: dict[str, dict[str, list[float]]] = {}
        model = xgb.train(
            parameters,
            train,
            num_boost_round=args.num_boost_round,
            evals=[(train, "train"), (validation, "validation")],
            evals_result=evaluation_results,
            callbacks=[
                xgb.callback.EarlyStopping(
                    rounds=args.early_stopping_rounds,
                    data_name="validation",
                    metric_name="auc",
                    save_best=True,
                )
            ],
        )
        best_iteration = int(model.best_iteration)
        mlflow.log_param("early_stopping_patience", args.early_stopping_rounds)
        mlflow.log_metric(
            "validation-auc",
            float(model.best_score),
            step=len(evaluation_results["validation"]["auc"]),
        )
        mlflow.log_metric("best_iteration", best_iteration)

        # Files under SM_MODEL_DIR become SageMaker's model.tar.gz after the job.
        model_dir = Path(os.environ["SM_MODEL_DIR"])
        model_dir.mkdir(parents=True, exist_ok=True)
        model.save_model(model_dir / "xgboost-model")


if __name__ == "__main__":
    main()
