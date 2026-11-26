"""Launch an XGBoost script-mode Training Job that logs with MLflow autolog."""

from __future__ import annotations

# ruff: noqa: E402, I001

import argparse
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

for logger_name in ("botocore.credentials", "sagemaker.config"):
    logging.getLogger(logger_name).setLevel(logging.WARNING)

import boto3
from sagemaker.core import image_uris
from sagemaker.core.helper.session_helper import Session
from sagemaker.core.shapes.shapes import OutputDataConfig, StoppingCondition, Tag
from sagemaker.core.training.configs import Compute, InputData, SourceCode
from sagemaker.train.model_trainer import ModelTrainer

from sagemaker_xgboost_lab.config import (
    AUTOLOG_XGBOOST_VERSION,
    DATASET_ID,
    DATASET_NAME,
    DEFAULT_INSTANCE_TYPE,
    EXPERIMENT_NAME,
    RANDOM_SEED,
    S3_PREFIX,
    AwsResources,
    load_aws_resources,
)
from sagemaker_xgboost_lab.data_prep import prepare_training_data


def run_autolog_training(resources: AwsResources, instance_type: str) -> None:
    """Prepare data locally and launch one script-mode SageMaker Training Job."""

    run_id = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    run_name = f"autolog-{run_id}"
    local_dir = Path("artifacts") / "autolog" / run_id

    print("[1/3] Prepare the public dataset and upload it to S3")
    prepared = prepare_training_data(local_dir / "data")
    boto_session = boto3.Session(region_name=resources.region)
    s3_client = boto_session.client("s3")
    # Script mode uploads the entry script and requirements file as extra S3 channels.
    sagemaker_session = Session(
        boto_session=boto_session,
        default_bucket=resources.bucket,
        default_bucket_prefix=S3_PREFIX,
    )
    s3_run_prefix = f"{S3_PREFIX}/runs/{run_id}"

    train_key = f"{s3_run_prefix}/data/train.csv"
    s3_client.upload_file(str(prepared.train_csv), resources.bucket, train_key)
    validation_key = f"{s3_run_prefix}/data/validation.csv"
    s3_client.upload_file(str(prepared.validation_csv), resources.bucket, validation_key)
    summary_key = f"{s3_run_prefix}/data/data-summary.json"
    s3_client.upload_file(str(prepared.summary_json), resources.bucket, summary_key)

    train_s3_uri = f"s3://{resources.bucket}/{train_key}"
    validation_s3_uri = f"s3://{resources.bucket}/{validation_key}"
    summary_s3_uri = f"s3://{resources.bucket}/{summary_key}"

    image_uri = str(
        image_uris.retrieve(
            framework="xgboost",
            region=resources.region,
            version=AUTOLOG_XGBOOST_VERSION,
            image_scope="training",
        )
    )
    source_dir = Path(__file__).resolve().parent

    print("[2/3] Run the script-mode SageMaker Training Job")
    trainer = ModelTrainer(
        sagemaker_session=sagemaker_session,
        role=resources.execution_role_arn,
        training_image=image_uri,
        source_code=SourceCode(
            source_dir=str(source_dir),
            entry_script="autolog.py",
            requirements="autolog-requirements.txt",
        ),
        base_job_name="xgboost-lab-autolog",
        compute=Compute(instance_type=instance_type, instance_count=1),
        stopping_condition=StoppingCondition(max_runtime_in_seconds=3600),
        output_data_config=OutputDataConfig(
            s3_output_path=f"s3://{resources.bucket}/{s3_run_prefix}/models/autolog"
        ),
        environment={
            "MLFLOW_TRACKING_URI": resources.mlflow_app_arn,
            "MLFLOW_EXPERIMENT_NAME": EXPERIMENT_NAME,
            "MLFLOW_RUN_NAME": run_name,
            "SAGEMAKER_IMAGE_URI": image_uri,
            "SAGEMAKER_INSTANCE_TYPE": instance_type,
            "DATASET_ID": str(DATASET_ID),
            "DATASET_NAME": DATASET_NAME,
            "TRAIN_S3_URI": train_s3_uri,
            "VALIDATION_S3_URI": validation_s3_uri,
            "DATA_SUMMARY_S3_URI": summary_s3_uri,
        },
        hyperparameters={
            "max_depth": 5,
            "eta": 0.08,
            "subsample": 0.9,
            "colsample_bytree": 0.9,
            "num_boost_round": 250,
            "early_stopping_rounds": 20,
            "seed": RANDOM_SEED,
        },
        tags=[
            Tag(key="Project", value=EXPERIMENT_NAME),
            Tag(key="Logging", value="mlflow-autolog"),
        ],
    )
    trainer.train(
        input_data_config=[
            InputData(
                channel_name="train",
                data_source=train_s3_uri,
                content_type="text/csv",
            ),
            InputData(
                channel_name="validation",
                data_source=validation_s3_uri,
                content_type="text/csv",
            ),
            InputData(
                channel_name="metadata",
                data_source=summary_s3_uri,
                content_type="application/json",
            ),
        ],
        wait=True,
        logs=True,
    )

    training_job = trainer._latest_training_job
    print("[3/3] Training and MLflow autologging complete")
    print(f"MLflow run: {run_name}")
    print(f"Training Job: {training_job.training_job_name}")
    print(f"Model artifact: {training_job.model_artifacts.s3_model_artifacts}")
    print(f"MLflow App: {resources.mlflow_app_arn}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run XGBoost in SageMaker script mode with MLflow autologging."
    )
    parser.add_argument(
        "--instance-type",
        default=DEFAULT_INSTANCE_TYPE,
        help="SageMaker Training instance. Default: %(default)s",
    )
    args = parser.parse_args()
    run_autolog_training(load_aws_resources(), args.instance_type)


if __name__ == "__main__":
    main()
