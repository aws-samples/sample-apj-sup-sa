"""Train one XGBoost model and record metrics in MLflow."""

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
    parser.add_argument("--num_boost_round", type=int, default=100)
    parser.add_argument("--early_stopping_rounds", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def load_channel(channel_dir: str, feature_names: list[str]) -> xgb.DMatrix:
    paths = list(Path(channel_dir).rglob("*.csv"))
    if len(paths) != 1:
        raise ValueError(f"Expected one CSV in {channel_dir}.")
    rows = np.loadtxt(paths[0], delimiter=",", dtype=np.float32, ndmin=2)
    if rows.shape[1] != len(feature_names) + 1 or not np.isfinite(rows).all():
        raise ValueError("Training CSV must contain a label and finite features matching metadata.")
    return xgb.DMatrix(rows[:, 1:], label=rows[:, 0], feature_names=feature_names)


def main() -> None:
    args = parse_args()
    metadata_path = Path(os.environ["SM_CHANNEL_METADATA"]) / "data-summary.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    feature_names = metadata["feature_names"]
    train = load_channel(os.environ["SM_CHANNEL_TRAIN"], feature_names)
    validation = load_channel(os.environ["SM_CHANNEL_VALIDATION"], feature_names)

    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    mlflow.set_experiment(os.environ["MLFLOW_EXPERIMENT_NAME"])
    # Model registration belongs to the Pipeline's CheckQuality branch.
    mlflow.xgboost.autolog(log_models=False, log_datasets=False, importance_types=[])

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
    execution_id = os.getenv("PIPELINE_EXECUTION_ID", "local")
    run_name = os.getenv("TRAINING_JOB_NAME", f"pipeline-{execution_id}")
    with mlflow.start_run(run_name=run_name):
        mlflow.set_tags(
            {
                "pipeline.execution_id": execution_id,
                "dataset.id": str(metadata["dataset_id"]),
                "dataset.sha256": metadata["source_sha256"],
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
        mlflow.log_metric("best-validation-auc", validation_auc)
        print(f"validation-auc={validation_auc:.6f}")

        model_dir = Path(os.environ["SM_MODEL_DIR"])
        model_dir.mkdir(parents=True, exist_ok=True)
        model.save_model(model_dir / "xgboost-model.json")


if __name__ == "__main__":
    main()
