"""Small helpers shared by the local SageMaker workflow."""

from __future__ import annotations

import tarfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import xgboost as xgb

from sagemaker_xgboost_lab.config import AwsResources

if TYPE_CHECKING:
    from sagemaker_xgboost_lab.train import TrainingResult


def is_gpu_instance(instance_type: str) -> bool:
    """Return whether this lab supports GPU training on the given instance."""

    return instance_type.startswith(("ml.g4dn.", "ml.g5."))


def download_model(s3_client: Any, model_s3_uri: str, destination: Path) -> xgb.Booster:
    bucket, key = _parse_s3_uri(model_s3_uri)
    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination / "model.tar.gz"
    s3_client.download_file(bucket, key, str(archive_path))
    extracted = destination / "extracted"
    extracted.mkdir(exist_ok=True)
    root = extracted.resolve()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            member_path = (extracted / member.name).resolve()
            if member_path != root and root not in member_path.parents:
                raise ValueError(f"Unsafe path in model artifact: {member.name}")
        archive.extractall(extracted)
    files = [path for path in extracted.rglob("*") if path.is_file()]
    if not files:
        raise FileNotFoundError(f"No model file found in {model_s3_uri}")
    model_path = next((path for path in files if path.name == "xgboost-model"), files[0])
    booster = xgb.Booster()
    booster.load_model(str(model_path))
    return booster


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"Expected an S3 URI: {uri}")
    bucket, separator, key = uri[5:].partition("/")
    if not bucket or not separator or not key:
        raise ValueError(f"Invalid S3 URI: {uri}")
    return bucket, key


def print_summary(
    run_id: str,
    resources: AwsResources,
    results: list[TrainingResult],
) -> None:
    best = max(results, key=lambda result: result.validation_auc)
    print()
    print(f"Experiment complete: {run_id}")
    for result in results:
        print(f"- {result.candidate}: validation AUC={result.validation_auc:.4f}")
        print(f"  Training Job: {result.training_job_name}")
        print(f"  Model: {result.model_s3_uri}")
    print(f"Best run: {best.candidate}")
    print(f"MLflow App: {resources.mlflow_app_arn}")
    print("Next: Register the selected run's model in the MLflow UI")
