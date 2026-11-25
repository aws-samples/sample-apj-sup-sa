"""XGBoost script-mode training entry point for AMT trials and managed MLflow autologging."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import mlflow
import numpy as np
import xgboost as xgb


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max_depth", type=int, default=5)
    parser.add_argument("--eta", type=float, default=0.1)
    parser.add_argument("--min_child_weight", type=float, default=1.0)
    parser.add_argument("--subsample", type=float, default=0.9)
    parser.add_argument("--colsample_bytree", type=float, default=0.9)
    parser.add_argument("--num_boost_round", type=int, default=300)
    parser.add_argument("--early_stopping_rounds", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def load_channel(channel_dir: str, feature_names: list[str]) -> xgb.DMatrix:
    csv_path = next(Path(channel_dir).rglob("*.csv"))
    rows = np.loadtxt(csv_path, delimiter=",", dtype=np.float32, ndmin=2)
    return xgb.DMatrix(rows[:, 1:], label=rows[:, 0], feature_names=feature_names)


def main() -> None:
    args = parse_args()
    metadata_path = next(Path(os.environ["SM_CHANNEL_METADATA"]).rglob("*.json"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    feature_names = metadata["feature_names"]
    train = load_channel(os.environ["SM_CHANNEL_TRAIN"], feature_names)
    validation = load_channel(os.environ["SM_CHANNEL_VALIDATION"], feature_names)

    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    mlflow.set_experiment(os.environ["MLFLOW_EXPERIMENT_NAME"])
    # In script mode we own xgb.train(), so MLflow can log parameters, metrics, and the model.
    mlflow.xgboost.autolog(log_input_examples=True, log_model_signatures=True, log_datasets=True)

    parameters = {
        "objective": "binary:logistic",
        "eval_metric": "auc",
        "tree_method": "hist",
        "max_depth": args.max_depth,
        "eta": args.eta,
        "min_child_weight": args.min_child_weight,
        "subsample": args.subsample,
        "colsample_bytree": args.colsample_bytree,
        "seed": args.seed,
    }
    with mlflow.start_run(run_name=os.environ["TRAINING_JOB_NAME"]):
        mlflow.set_tags(
            {
                "pipeline.execution_id": os.environ["PIPELINE_EXECUTION_ID"],
                "sagemaker.training_job_name": os.environ["TRAINING_JOB_NAME"],
                "data.summary_s3_uri": os.environ["DATA_SUMMARY_S3_URI"],
            }
        )
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
        validation_auc = float(model.best_score)
        mlflow.log_metric("validation-auc", validation_auc, step=model.best_iteration)
        # AMT extracts this stable line with its MetricDefinition regex.
        print(f"validation-auc={validation_auc:.6f}")

        model_dir = Path(os.environ["SM_MODEL_DIR"])
        model_dir.mkdir(parents=True, exist_ok=True)
        model.save_model(model_dir / "xgboost-model.json")


if __name__ == "__main__":
    main()
