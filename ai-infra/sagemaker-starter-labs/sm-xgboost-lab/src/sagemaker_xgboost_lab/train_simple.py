"""Run one SageMaker built-in XGBoost Training Job without MLflow."""

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
from sagemaker.core.training.configs import Compute, InputData
from sagemaker.train.model_trainer import ModelTrainer

from sagemaker_xgboost_lab.config import (
    DEFAULT_INSTANCE_TYPE,
    EXPERIMENT_NAME,
    RANDOM_SEED,
    S3_PREFIX,
    XGBOOST_VERSION,
    TrainingResources,
    load_training_resources,
)
from sagemaker_xgboost_lab.data_prep import prepare_training_data


SIMPLE_HYPERPARAMETERS = {
    "objective": "binary:logistic",
    "eval_metric": "auc",
    "tree_method": "hist",
    "num_round": 300,
    "max_depth": 5,
    "eta": 0.08,
    "subsample": 0.9,
    "colsample_bytree": 0.9,
    "seed": RANDOM_SEED,
    "verbosity": 1,
}


def run_training(resources: TrainingResources, instance_type: str) -> None:
    """Prepare data locally and run one built-in XGBoost job in SageMaker."""

    run_id = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    local_dir = Path("artifacts") / "simple" / run_id
    s3_run_prefix = f"{S3_PREFIX}/runs/{run_id}"

    print("[1/3] Prepare the public dataset and upload it to S3")
    prepared = prepare_training_data(local_dir / "data")
    boto_session = boto3.Session(region_name=resources.region)
    s3_client = boto_session.client("s3")

    train_key = f"{s3_run_prefix}/data/train.csv"
    s3_client.upload_file(str(prepared.train_csv), resources.bucket, train_key)
    train_s3_uri = f"s3://{resources.bucket}/{train_key}"

    validation_key = f"{s3_run_prefix}/data/validation.csv"
    s3_client.upload_file(str(prepared.validation_csv), resources.bucket, validation_key)
    validation_s3_uri = f"s3://{resources.bucket}/{validation_key}"

    # SageMaker SDK v3 uses this session, image, role, compute, and output path to create the Job.
    sagemaker_session = Session(boto_session=boto_session)
    image_uri = str(
        image_uris.retrieve(
            framework="xgboost",
            region=resources.region,
            version=XGBOOST_VERSION,
            image_scope="training",
        )
    )
    trainer = ModelTrainer(
        sagemaker_session=sagemaker_session,
        role=resources.execution_role_arn,
        training_image=image_uri,
        base_job_name="xgboost-lab-simple",
        compute=Compute(instance_type=instance_type, instance_count=1),
        stopping_condition=StoppingCondition(max_runtime_in_seconds=3600),
        output_data_config=OutputDataConfig(
            s3_output_path=f"s3://{resources.bucket}/{s3_run_prefix}/models/simple"
        ),
        hyperparameters=SIMPLE_HYPERPARAMETERS,
        tags=[Tag(key="Project", value=EXPERIMENT_NAME), Tag(key="Example", value="simple")],
    )

    print(f"[2/3] Run one SageMaker Training Job ({instance_type})")
    # Built-in XGBoost reads named S3 channels; no user training script runs in the container.
    trainer.train(
        input_data_config=[
            InputData(channel_name="train", data_source=train_s3_uri, content_type="text/csv"),
            InputData(
                channel_name="validation",
                data_source=validation_s3_uri,
                content_type="text/csv",
            ),
        ],
        wait=True,
        logs=True,
    )

    training_job = trainer._latest_training_job
    validation_auc = next(
        metric.value
        for metric in training_job.final_metric_data_list
        if metric.metric_name == "validation:auc"
    )
    print("[3/3] Training complete")
    print(f"Training Job: {training_job.training_job_name}")
    print(f"Validation AUC: {validation_auc:.4f}")
    print(f"Model: {training_job.model_artifacts.s3_model_artifacts}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one SageMaker built-in XGBoost Training Job without MLflow."
    )
    parser.add_argument(
        "--instance-type",
        default=DEFAULT_INSTANCE_TYPE,
        help="SageMaker Training instance. Default: %(default)s",
    )
    args = parser.parse_args()
    run_training(load_training_resources(), args.instance_type)


if __name__ == "__main__":
    main()
