"""Compare the AMT candidate and the currently deployed model on the same OOT dataset."""

from __future__ import annotations

import argparse
import json
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory

import boto3
import numpy as np
import xgboost as xgb
from sklearn.metrics import brier_score_loss, roc_auc_score


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--production-model-package-arn", default="")
    parser.add_argument("--min-auc-delta", type=float, default=0.01)
    parser.add_argument("--max-brier-regression", type=float, default=0.005)
    parser.add_argument("--first-model-min-auc", type=float, default=0.65)
    parser.add_argument("--first-model-max-brier", type=float, default=0.25)
    parser.add_argument("--output-dir", default="/opt/ml/processing/output/evaluation")
    return parser.parse_args()


def load_model(model_tar: Path) -> xgb.Booster:
    with TemporaryDirectory() as temp_dir:
        with tarfile.open(model_tar) as archive:
            archive.extractall(temp_dir)
        model_path = next(Path(temp_dir).rglob("xgboost-model.json"))
        model = xgb.Booster()
        model.load_model(model_path)
        return model


def score(model: xgb.Booster, features: np.ndarray, labels: np.ndarray) -> dict[str, float]:
    probabilities = model.predict(xgb.DMatrix(features, feature_names=model.feature_names))
    thresholds = np.unique(probabilities)

    def distribution_gap(threshold: float) -> float:
        bad_cdf = (probabilities[labels == 1] <= threshold).mean()
        good_cdf = (probabilities[labels == 0] <= threshold).mean()
        return abs(bad_cdf - good_cdf)

    ks = max(distribution_gap(threshold) for threshold in thresholds)
    return {
        "roc_auc": float(roc_auc_score(labels, probabilities)),
        "brier_score": float(brier_score_loss(labels, probabilities)),
        "ks": float(ks),
    }


def download_champion(model_package_arn: str, destination: Path) -> Path:
    package = boto3.client("sagemaker").describe_model_package(ModelPackageName=model_package_arn)
    model_uri = package["InferenceSpecification"]["Containers"][0]["ModelDataUrl"]
    bucket, key = model_uri.removeprefix("s3://").split("/", 1)
    boto3.client("s3").download_file(bucket, key, str(destination))
    return destination


def main() -> None:
    args = parse_args()
    oot_csv = next(Path("/opt/ml/processing/oot").rglob("*.csv"))
    rows = np.loadtxt(oot_csv, delimiter=",", dtype=np.float32, ndmin=2)
    labels, features = rows[:, 0], rows[:, 1:]
    candidate_tar = next(Path("/opt/ml/processing/candidate").rglob("*.tar.gz"))
    candidate = score(load_model(candidate_tar), features, labels)

    champion: dict[str, float] | None = None
    if args.production_model_package_arn:
        champion_tar = download_champion(
            args.production_model_package_arn, Path("/tmp/champion-model.tar.gz")
        )
        champion = score(load_model(champion_tar), features, labels)
        auc_delta = candidate["roc_auc"] - champion["roc_auc"]
        brier_delta = candidate["brier_score"] - champion["brier_score"]
        passes = auc_delta >= args.min_auc_delta and brier_delta <= args.max_brier_regression
    else:
        auc_delta = candidate["roc_auc"]
        brier_delta = candidate["brier_score"]
        passes = (
            candidate["roc_auc"] >= args.first_model_min_auc
            and candidate["brier_score"] <= args.first_model_max_brier
        )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    evaluation = {
        "candidate": candidate,
        "champion": champion,
        "decision": {"auc_delta": auc_delta, "brier_delta": brier_delta, "passes": passes},
    }
    (output_dir / "evaluation.json").write_text(json.dumps(evaluation, indent=2), encoding="utf-8")
    (output_dir / "model-quality.json").write_text(
        json.dumps({"binary_classification_metrics": candidate}, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
