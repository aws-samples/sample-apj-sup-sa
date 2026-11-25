"""Run two SageMaker XGBoost jobs and compare them in managed MLflow."""

from __future__ import annotations

# ruff: noqa: E402, I001

import argparse
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

for logger_name in ("botocore.credentials", "sagemaker.config"):
    logging.getLogger(logger_name).setLevel(logging.WARNING)

import boto3
import mlflow
import sagemaker_mlflow
import xgboost as xgb
from mlflow.models import infer_signature
from sagemaker.core import image_uris
from sagemaker.core.helper.session_helper import Session
from sagemaker.core.shapes.shapes import OutputDataConfig, StoppingCondition, Tag
from sagemaker.core.training.configs import Compute, InputData
from sagemaker.train.model_trainer import ModelTrainer

from sagemaker_xgboost_lab.config import (
    DATASET_ID,
    DATASET_NAME,
    DEFAULT_INSTANCE_TYPE,
    EXPERIMENT_NAME,
    RANDOM_SEED,
    S3_PREFIX,
    XGBOOST_VERSION,
    AwsResources,
    load_aws_resources,
)
from sagemaker_xgboost_lab.data_prep import prepare_training_data
from sagemaker_xgboost_lab.util import (
    download_model,
    is_gpu_instance,
    print_summary,
)


@dataclass(frozen=True)
class TrainingResult:
    candidate: str
    training_job_name: str
    model_s3_uri: str
    validation_auc: float


def run_experiment(resources: AwsResources, instance_type: str) -> list[TrainingResult]:
    """Prepare data, run two managed jobs, and log each result to MLflow."""

    # Scope all S3 inputs, model outputs, and local artifacts to this experiment.
    run_id = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    local_dir = Path("artifacts") / run_id
    print("[1/4] Connect to the MLflow App")
    mlflow.set_tracking_uri(resources.mlflow_app_arn)
    mlflow.set_experiment(EXPERIMENT_NAME)

    print("[2/4] Prepare the public dataset and upload it to S3")
    # SageMaker Training Jobs take S3 URIs as input, not local file paths.
    prepared = prepare_training_data(local_dir / "data")

    boto_session = boto3.Session(region_name=resources.region)
    s3_client = boto_session.client("s3")

    # Session provides the Boto3 session and SageMaker API client used by SDK v3 resources.
    sagemaker_session = Session(boto_session=boto_session)
    s3_run_prefix = f"{S3_PREFIX}/runs/{run_id}"

    train_key = f"{s3_run_prefix}/data/train.csv"
    s3_client.upload_file(str(prepared.train_csv), resources.bucket, train_key)
    train_s3_uri = f"s3://{resources.bucket}/{train_key}"

    validation_key = f"{s3_run_prefix}/data/validation.csv"
    s3_client.upload_file(str(prepared.validation_csv), resources.bucket, validation_key)
    validation_s3_uri = f"s3://{resources.bucket}/{validation_key}"

    summary_key = f"{s3_run_prefix}/data/data-summary.json"
    s3_client.upload_file(str(prepared.summary_json), resources.bucket, summary_key)

    # Select the AWS-provided built-in training image for this region and XGBoost version.
    image_uri = str(
        image_uris.retrieve(
            framework="xgboost",
            region=resources.region,
            version=XGBOOST_VERSION,
            image_scope="training",
        )
    )

    # Compare default and tuned settings as separate Training Jobs on the same data split.
    candidates = candidate_hyperparameters(instance_type)
    results: list[TrainingResult] = []
    for index, (candidate, hyperparameters) in enumerate(candidates.items(), start=1):
        print(f"[3/4] SageMaker Training Job {index}/{len(candidates)}: {candidate}")
        # Define the Training Job's image, role, instance, and output location in SDK v3.
        trainer = ModelTrainer(
            sagemaker_session=sagemaker_session,
            role=resources.execution_role_arn,
            training_image=image_uri,
            base_job_name=f"xgboost-lab-{candidate}",
            compute=Compute(
                instance_type=instance_type,
                instance_count=1,
            ),
            stopping_condition=StoppingCondition(max_runtime_in_seconds=3600),
            output_data_config=OutputDataConfig(
                s3_output_path=f"s3://{resources.bucket}/{s3_run_prefix}/models/{candidate}"
            ),
            hyperparameters=hyperparameters,
            tags=[
                Tag(key="Project", value=EXPERIMENT_NAME),
                Tag(key="Candidate", value=candidate),
            ],
        )
        # An MLflow run groups this candidate's parameters, metrics, and artifacts.
        with mlflow.start_run(run_name=f"{candidate}-{run_id}") as active_run:
            mlflow.set_tags(
                {
                    "project": EXPERIMENT_NAME,
                    "candidate": candidate,
                    "run_id": run_id,
                    "sagemaker.instance_type": instance_type,
                }
            )
            mlflow.log_params(
                {
                    **{f"xgboost.{key}": value for key, value in hyperparameters.items()},
                    "sagemaker.image_uri": image_uri,
                    "sagemaker.instance_type": instance_type,
                    "dataset.uci_id": DATASET_ID,
                    "dataset.name": DATASET_NAME,
                    "dataset.positive_label": "1 = default payment next month",
                }
            )
            mlflow.log_input(
                mlflow.data.from_pandas(
                    prepared.splits.x_train.assign(label=prepared.splits.y_train),
                    source=train_s3_uri,
                    targets="label",
                    name="train",
                ),
                context="training",
            )
            mlflow.log_input(
                mlflow.data.from_pandas(
                    prepared.splits.x_validation.assign(label=prepared.splits.y_validation),
                    source=validation_s3_uri,
                    targets="label",
                    name="validation",
                ),
                context="validation",
            )
            # The built-in XGBoost container reads named S3 channels
            # without a user-provided script.
            # `train` is for training; the container uses `validation` to compute validation AUC.
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
                ],
                # After the job finishes, run local validation using the generated model artifact.
                wait=True,
                logs=True,
            )
            # ModelTrainer holds the most recently created SDK v3 TrainingJob resource.
            training_job = trainer._latest_training_job
            training_job_name = training_job.training_job_name
            model_s3_uri = training_job.model_artifacts.s3_model_artifacts
            validation_auc = next(
                metric.value
                for metric in training_job.final_metric_data_list
                if metric.metric_name == "validation:auc"
            )
            mlflow.log_metric("validation-auc", validation_auc)

            # Load the SageMaker model artifact as a Booster to create an MLflow Logged Model.
            booster = download_model(
                s3_client,
                model_s3_uri,
                local_dir / "models" / candidate,
            )
            mlflow.log_artifact(str(prepared.summary_json), artifact_path="data")
            input_example = prepared.splits.x_validation.head(3).to_numpy()
            prediction_example = booster.predict(xgb.DMatrix(input_example))
            model_info = mlflow.xgboost.log_model(
                booster,
                name="model",
                signature=infer_signature(input_example, prediction_example),
                input_example=input_example,
            )
            # Also log the image/model artifact pair that Batch Transform needs to MLflow.
            sagemaker_mlflow.log_inference_specification(
                model_info.model_id,
                inference_specification={
                    "Containers": [
                        {
                            "Image": image_uri,
                            "ModelDataUrl": model_s3_uri,
                        }
                    ],
                    "SupportedContentTypes": ["text/csv"],
                    "SupportedResponseMIMETypes": ["text/csv"],
                    "SupportedTransformInstanceTypes": [
                        "ml.c5.xlarge",
                    ],
                },
            )
            mlflow.set_tags(
                {
                    "sagemaker.training_job_name": training_job_name,
                    "sagemaker.model_s3_uri": model_s3_uri,
                    "mlflow_run_id": active_run.info.run_id,
                    "mlflow_model_id": model_info.model_id,
                }
            )
            results.append(
                TrainingResult(
                    candidate=candidate,
                    training_job_name=training_job_name,
                    model_s3_uri=model_s3_uri,
                    validation_auc=validation_auc,
                )
            )

    print("[4/4] Complete MLflow logging and result comparison")
    print_summary(run_id, resources, results)
    return results


def candidate_hyperparameters(instance_type: str) -> dict[str, dict[str, Any]]:
    """Return two readable configurations for an MLflow comparison."""

    # If the selected instance has a GPU, switch only the tree method to GPU histogram.
    tree_method = "gpu_hist" if is_gpu_instance(instance_type) else "hist"
    shared = {
        "objective": "binary:logistic",
        "eval_metric": "auc",
        "tree_method": tree_method,
        "seed": RANDOM_SEED,
        "verbosity": 1,
    }
    return {
        # Intentionally small so the sample shows a visible improvement after tuning.
        "default": {
            **shared,
            "num_round": 10,
            "max_depth": 1,
            "eta": 0.1,
        },
        "tuned": {
            **shared,
            "num_round": 250,
            "max_depth": 5,
            "eta": 0.08,
            "subsample": 0.9,
            "colsample_bytree": 0.9,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run two SageMaker XGBoost jobs and compare them in MLflow."
    )
    parser.add_argument(
        "--instance-type",
        default=DEFAULT_INSTANCE_TYPE,
        help="SageMaker Training instance. Default: %(default)s",
    )
    args = parser.parse_args()

    for logger_name in ("botocore.credentials", "sagemaker.config"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    run_experiment(load_aws_resources(), args.instance_type)


if __name__ == "__main__":
    main()
