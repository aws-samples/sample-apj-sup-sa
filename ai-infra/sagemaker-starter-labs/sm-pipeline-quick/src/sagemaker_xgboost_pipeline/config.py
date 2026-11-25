"""Names and environment-specific resources for the XGBoost Pipeline reference."""

from __future__ import annotations

import os
from dataclasses import dataclass

AWS_REGION = "ap-northeast-2"
PIPELINE_NAME = "sagemaker-xgboost-lab-pipeline"
PIPELINE_PREFIX = "sagemaker-xgboost-lab/credit-card-default/pipeline"
MLFLOW_EXPERIMENT_NAME = "sagemaker-xgboost-lab-pipeline"
MODEL_PACKAGE_GROUP_NAME = "sagemaker-xgboost-lab-pipeline"
LABEL_COLUMN = "default_payment_next_month"


@dataclass(frozen=True)
class Resources:
    region: str
    bucket: str
    execution_role_arn: str
    processing_image_uri: str
    mlflow_app_arn: str
    redshift_workgroup_name: str
    redshift_database: str
    redshift_unload_role_arn: str
    model_package_group_name: str

    @classmethod
    def from_environment(cls) -> Resources:
        values = {
            "bucket": os.getenv("S3_BUCKET"),
            "execution_role_arn": os.getenv("SAGEMAKER_EXECUTION_ROLE_ARN"),
            "processing_image_uri": os.getenv("PROCESSING_IMAGE_URI"),
            "mlflow_app_arn": os.getenv("MLFLOW_APP_ARN"),
            "redshift_workgroup_name": os.getenv("REDSHIFT_WORKGROUP_NAME"),
            "redshift_database": os.getenv("REDSHIFT_DATABASE"),
            "redshift_unload_role_arn": os.getenv("REDSHIFT_UNLOAD_ROLE_ARN"),
            "model_package_group_name": os.getenv(
                "MODEL_PACKAGE_GROUP_NAME",
                MODEL_PACKAGE_GROUP_NAME,
            ),
        }
        missing = [name.upper() for name, value in values.items() if not value]
        if missing:
            raise RuntimeError(f"Missing settings: {', '.join(missing)}")
        return cls(region=os.getenv("AWS_REGION", AWS_REGION), **values)  # type: ignore[arg-type]
