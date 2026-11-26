"""Project configuration and AWS resource loading."""

from __future__ import annotations

import os
from dataclasses import dataclass

AWS_REGION = "ap-northeast-2"
DATASET_ID = 350
DATASET_NAME = "UCI Default of Credit Card Clients"
EXPERIMENT_NAME = "sagemaker-xgboost-lab"
S3_PREFIX = "sagemaker-xgboost-lab/credit-card-default"
XGBOOST_VERSION = "1.7-1"
AUTOLOG_XGBOOST_VERSION = "3.0-5"
DEFAULT_INSTANCE_TYPE = "ml.c6i.xlarge"
VALIDATION_FRACTION = 0.2
RANDOM_SEED = 42


@dataclass(frozen=True)
class TrainingResources:
    region: str
    execution_role_arn: str
    bucket: str


@dataclass(frozen=True)
class AwsResources(TrainingResources):
    mlflow_app_arn: str


def load_training_resources() -> TrainingResources:
    """Read the AWS values needed by a SageMaker Training Job."""

    required = {
        "SAGEMAKER_EXECUTION_ROLE_ARN": os.getenv("SAGEMAKER_EXECUTION_ROLE_ARN"),
        "S3_BUCKET": os.getenv("S3_BUCKET"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        names = ", ".join(missing)
        raise RuntimeError(f"Missing AWS settings: {names}. Use a launcher in ./scripts.")
    return TrainingResources(
        region=os.getenv("AWS_REGION", AWS_REGION),
        execution_role_arn=str(required["SAGEMAKER_EXECUTION_ROLE_ARN"]),
        bucket=str(required["S3_BUCKET"]),
    )


def load_aws_resources() -> AwsResources:
    """Read the AWS values needed by SageMaker Training and managed MLflow."""

    training_resources = load_training_resources()
    mlflow_app_arn = os.getenv("MLFLOW_APP_ARN")
    if not mlflow_app_arn:
        raise RuntimeError("Missing AWS settings: MLFLOW_APP_ARN. Use a launcher in ./scripts.")
    return AwsResources(
        region=training_resources.region,
        execution_role_arn=training_resources.execution_role_arn,
        bucket=training_resources.bucket,
        mlflow_app_arn=mlflow_app_arn,
    )
