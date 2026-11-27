"""Names and environment-specific resources for the UCI XGBoost Pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

AWS_REGION = "ap-northeast-2"
PIPELINE_NAME = "sagemaker-xgboost-lab-pipeline"
PIPELINE_PREFIX = "sagemaker-xgboost-lab/credit-card-default/pipeline"
MLFLOW_EXPERIMENT_NAME = "sagemaker-xgboost-lab-pipeline"
MODEL_PACKAGE_GROUP_NAME = "sagemaker-xgboost-lab-pipeline"
XGBOOST_VERSION = "3.0-5"


@dataclass(frozen=True)
class Resources:
    region: str
    bucket: str
    execution_role_arn: str
    processing_image_uri: str
    input_data_s3_uri: str
    mlflow_app_arn: str
    model_package_group_name: str

    @classmethod
    def from_environment(cls) -> Resources:
        values = {
            "bucket": os.getenv("S3_BUCKET"),
            "execution_role_arn": os.getenv("SAGEMAKER_EXECUTION_ROLE_ARN"),
            "processing_image_uri": os.getenv("PROCESSING_IMAGE_URI"),
            "input_data_s3_uri": os.getenv("INPUT_DATA_S3_URI"),
            "mlflow_app_arn": os.getenv("MLFLOW_APP_ARN"),
            "model_package_group_name": os.getenv(
                "MODEL_PACKAGE_GROUP_NAME",
                MODEL_PACKAGE_GROUP_NAME,
            ),
        }
        missing = [name.upper() for name, value in values.items() if not value]
        if missing:
            raise RuntimeError(f"Missing settings: {', '.join(missing)}")
        location = urlparse(values["input_data_s3_uri"])
        if location.scheme != "s3" or not location.netloc or not location.path.strip("/"):
            raise ValueError("INPUT_DATA_S3_URI must identify an S3 input prefix.")
        return cls(region=os.getenv("AWS_REGION", AWS_REGION), **values)  # type: ignore[arg-type]
