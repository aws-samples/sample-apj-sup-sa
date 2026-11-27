"""Evaluate one XGBoost model on held-out test data and write a Pipeline property file."""

from __future__ import annotations

import argparse
import json
import tarfile
from pathlib import Path

import numpy as np
import xgboost as xgb
from sklearn.metrics import roc_auc_score


def load_model(model_tar: Path) -> xgb.Booster:
    with tarfile.open(model_tar) as archive:
        members = [
            member
            for member in archive.getmembers()
            if member.isfile() and member.name.removeprefix("./") == "xgboost-model.json"
        ]
        if len(members) != 1:
            raise ValueError("Model archive must contain exactly one xgboost-model.json.")
        with archive.extractfile(members[0]) as model_file:
            model = xgb.Booster()
            model.load_model(bytearray(model_file.read()))
    return model


def score(model: xgb.Booster, features: np.ndarray, labels: np.ndarray) -> float:
    if set(np.unique(labels)) != {0, 1}:
        raise ValueError("Test data must contain labels 0 and 1.")
    probabilities = model.predict(xgb.DMatrix(features, feature_names=model.feature_names))
    return float(roc_auc_score(labels, probabilities))


def evaluate_model(test_dir: Path, model_dir: Path, output_dir: Path) -> dict:
    test_files = list(test_dir.rglob("*.csv"))
    model_files = list(model_dir.rglob("*.tar.gz"))
    if len(test_files) != 1 or len(model_files) != 1:
        raise ValueError("Expected one test CSV and one model archive.")
    rows = np.loadtxt(test_files[0], delimiter=",", dtype=np.float32, ndmin=2)
    auc = score(load_model(model_files[0]), rows[:, 1:], rows[:, 0])
    report = {"binary_classification_metrics": {"auc": {"value": auc}}, "test_rows": len(rows)}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-dir", type=Path, default=Path("/opt/ml/processing/test"))
    parser.add_argument("--model-dir", type=Path, default=Path("/opt/ml/processing/model"))
    parser.add_argument(
        "--output-dir", type=Path, default=Path("/opt/ml/processing/output/evaluation")
    )
    args = parser.parse_args()
    print(json.dumps(evaluate_model(args.test_dir, args.model_dir, args.output_dir)))


if __name__ == "__main__":
    main()
