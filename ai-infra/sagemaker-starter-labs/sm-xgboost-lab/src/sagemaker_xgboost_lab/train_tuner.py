"""Tune the SageMaker built-in XGBoost model with Automatic Model Tuning."""

from __future__ import annotations

# ruff: noqa: E402, I001

import argparse
import logging
import uuid
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
from sagemaker.core.parameter import ContinuousParameter, IntegerParameter
from sagemaker.core.shapes.shapes import (
    Channel,
    DataSource,
    OutputDataConfig,
    S3DataSource,
    StoppingCondition,
)
from sagemaker.core.training.configs import Compute
from sagemaker.train.model_trainer import ModelTrainer
from sagemaker.train.tuner import HyperparameterTuner

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
from sagemaker_xgboost_lab.util import download_model, is_gpu_instance


# A broad search covers more of the space but needs correspondingly more trials.
HYPERPARAMETER_RANGES = {
    "eta": ContinuousParameter(0.005, 0.2, scaling_type="Logarithmic"),
    "max_depth": IntegerParameter(2, 10),
    "min_child_weight": ContinuousParameter(0.5, 100, scaling_type="Logarithmic"),
    "subsample": ContinuousParameter(0.5, 1.0),
    "colsample_bytree": ContinuousParameter(0.4, 1.0),
    "gamma": ContinuousParameter(0, 10),
    "lambda": ContinuousParameter(0.1, 100, scaling_type="Logarithmic"),
}
MAX_BOOST_ROUNDS = 1200
BOOSTER_EARLY_STOPPING_ROUNDS = 50
MAX_TRIAL_RUNS_TO_LOG = 100
TUNING_TAGS = {
    "Project": EXPERIMENT_NAME,
    "TrainingType": "AutomaticModelTuning",
}


def log_trial_runs(
    trials: list[dict[str, Any]],
    static_hyperparameters: dict[str, Any],
    tuning_job_name: str,
) -> None:
    """Log compact child runs without duplicating the dataset or model artifacts."""

    for rank, trial in enumerate(trials, start=1):
        training_job_name = str(trial["training_job_name"])
        with mlflow.start_run(
            run_name=f"trial-{rank:02d}-{training_job_name.rsplit('-', maxsplit=1)[-1]}",
            nested=True,
        ):
            mlflow.log_params(
                {
                    **{
                        f"xgboost.{name}": value
                        for name, value in static_hyperparameters.items()
                    },
                    **{
                        f"xgboost.{name}": value
                        for name, value in trial["hyperparameters"].items()
                    },
                    "amt.rank": rank,
                }
            )
            metric = trial["objective_metric"]
            if metric is not None:
                mlflow.log_metric("validation-auc", float(metric))
            mlflow.set_tags(
                {
                    "training_type": "automatic_model_tuning_trial",
                    "sagemaker.tuning_job_name": tuning_job_name,
                    "sagemaker.training_job_name": training_job_name,
                    "sagemaker.training_job_status": str(trial["status"]),
                }
            )


def run_tuning(
    resources: AwsResources,
    instance_type: str,
    max_jobs: int,
    max_parallel_jobs: int,
) -> None:
    run_id = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    tuning_job_name = f"xgb-amt-{run_id}"
    local_dir = Path("artifacts") / "tuning" / run_id

    print("[1/5] Connect to the MLflow App")
    mlflow.set_tracking_uri(resources.mlflow_app_arn)
    mlflow.set_experiment(EXPERIMENT_NAME)

    print("[2/5] Prepare the public dataset and upload it to S3")
    prepared = prepare_training_data(local_dir / "data")
    boto_session = boto3.Session(region_name=resources.region)
    s3_client = boto_session.client("s3")
    sagemaker_client = boto_session.client("sagemaker")
    sagemaker_session = Session(boto_session=boto_session)
    s3_run_prefix = f"{S3_PREFIX}/runs/{run_id}"

    train_key = f"{s3_run_prefix}/data/train.csv"
    s3_client.upload_file(str(prepared.train_csv), resources.bucket, train_key)
    train_s3_uri = f"s3://{resources.bucket}/{train_key}"

    validation_key = f"{s3_run_prefix}/data/validation.csv"
    s3_client.upload_file(str(prepared.validation_csv), resources.bucket, validation_key)
    validation_s3_uri = f"s3://{resources.bucket}/{validation_key}"

    image_uri = str(
        image_uris.retrieve(
            framework="xgboost",
            region=resources.region,
            version=XGBOOST_VERSION,
            image_scope="training",
        )
    )
    static_hyperparameters = {
        "objective": "binary:logistic",
        "eval_metric": "auc",
        # num_round is an upper bound; stop early if validation AUC does not improve for 50 rounds.
        "num_round": MAX_BOOST_ROUNDS,
        "early_stopping_rounds": BOOSTER_EARLY_STOPPING_ROUNDS,
        "tree_method": "gpu_hist" if is_gpu_instance(instance_type) else "hist",
        "seed": RANDOM_SEED,
        "verbosity": 1,
    }

    # ModelTrainer defines the image, role, compute, and fixed hyperparameters shared by all trials.
    trainer = ModelTrainer(
        sagemaker_session=sagemaker_session,
        role=resources.execution_role_arn,
        training_image=image_uri,
        base_job_name="xgboost-lab-amt",
        compute=Compute(instance_type=instance_type, instance_count=1),
        stopping_condition=StoppingCondition(max_runtime_in_seconds=3600),
        output_data_config=OutputDataConfig(
            s3_output_path=f"s3://{resources.bucket}/{s3_run_prefix}/models/amt"
        ),
        hyperparameters=static_hyperparameters,
    )

    # Auto early stopping here stops unpromising trials, not boosting rounds.
    tuner = HyperparameterTuner(
        model_trainer=trainer,
        objective_metric_name="validation:auc",
        hyperparameter_ranges=HYPERPARAMETER_RANGES,
        strategy="Bayesian",
        objective_type="Maximize",
        max_jobs=max_jobs,
        max_parallel_jobs=max_parallel_jobs,
        early_stopping_type="Auto",
        random_seed=RANDOM_SEED,
        tags=TUNING_TAGS,
    )

    print(f"[3/5] Run Automatic Model Tuning ({max_jobs} training jobs)")
    mlflow_run_id = ""
    with mlflow.start_run(run_name=f"amt-{run_id}") as active_run:
        mlflow_run_id = active_run.info.run_id
        mlflow.log_params(
            {
                "amt.strategy": "Bayesian",
                "amt.max_jobs": max_jobs,
                "amt.max_parallel_jobs": max_parallel_jobs,
                "amt.objective_metric": "validation:auc",
                "search.eta": "0.005-0.2 (log)",
                "search.max_depth": "2-10",
                "search.min_child_weight": "0.5-100 (log)",
                "search.subsample": "0.5-1.0",
                "search.colsample_bytree": "0.4-1.0",
                "search.gamma": "0-10",
                "search.lambda": "0.1-100 (log)",
                **{
                    f"xgboost.static.{name}": value
                    for name, value in static_hyperparameters.items()
                },
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
        tuner.tune(
            inputs=[
                Channel(
                    channel_name="train",
                    data_source=DataSource(
                        s3_data_source=S3DataSource(
                            s3_data_type="S3Prefix",
                            s3_uri=train_s3_uri,
                            s3_data_distribution_type="FullyReplicated",
                        )
                    ),
                    content_type="text/csv",
                ),
                Channel(
                    channel_name="validation",
                    data_source=DataSource(
                        s3_data_source=S3DataSource(
                            s3_data_type="S3Prefix",
                            s3_uri=validation_s3_uri,
                            s3_data_distribution_type="FullyReplicated",
                        )
                    ),
                    content_type="text/csv",
                ),
            ],
            job_name=tuning_job_name,
            wait=True,
        )

        print("[4/5] Read the best training job")
        best_training_job_name = tuner.best_training_job()
        best_job = sagemaker_client.describe_training_job(TrainingJobName=best_training_job_name)
        tuning_job = sagemaker_client.describe_hyper_parameter_tuning_job(
            HyperParameterTuningJobName=tuning_job_name
        )
        best_parameters = {
            name: best_job["HyperParameters"][name] for name in HYPERPARAMETER_RANGES
        }
        validation_auc = tuning_job["BestTrainingJob"][
            "FinalHyperParameterTuningJobObjectiveMetric"
        ]["Value"]
        model_s3_uri = best_job["ModelArtifacts"]["S3ModelArtifacts"]

        trial_summaries = sagemaker_client.list_training_jobs_for_hyper_parameter_tuning_job(
            HyperParameterTuningJobName=tuning_job_name,
            SortBy="FinalObjectiveMetricValue",
            SortOrder="Descending",
            MaxResults=max_jobs,
        )["TrainingJobSummaries"]
        trials = [
            {
                "training_job_name": trial["TrainingJobName"],
                "status": trial["TrainingJobStatus"],
                "objective_metric": trial.get(
                    "FinalHyperParameterTuningJobObjectiveMetric", {}
                ).get("Value"),
                "hyperparameters": trial.get("TunedHyperParameters", {}),
            }
            for trial in trial_summaries
        ]

        print("[5/5] Log the best model and tuning results to MLflow")
        if len(trials) <= MAX_TRIAL_RUNS_TO_LOG:
            log_trial_runs(trials, static_hyperparameters, tuning_job_name)
            mlflow.set_tag("mlflow.trial_logging", "child_runs")
        else:
            mlflow.set_tag("mlflow.trial_logging", "summary_only")
        mlflow.log_params({f"best.{name}": value for name, value in best_parameters.items()})
        mlflow.log_metric("validation-auc", validation_auc)
        mlflow.log_dict({"trials": trials}, "tuning/trials.json")
        mlflow.log_artifact(str(prepared.summary_json), artifact_path="data")

        booster = download_model(
            s3_client,
            model_s3_uri,
            local_dir / "model",
        )
        input_example = prepared.splits.x_validation.head(3).to_numpy()
        prediction_example = booster.predict(xgb.DMatrix(input_example))
        model_info = mlflow.xgboost.log_model(
            booster,
            name="model",
            signature=infer_signature(input_example, prediction_example),
            input_example=input_example,
        )
        sagemaker_mlflow.log_inference_specification(
            model_info.model_id,
            inference_specification={
                "Containers": [{"Image": image_uri, "ModelDataUrl": model_s3_uri}],
                "SupportedContentTypes": ["text/csv"],
                "SupportedResponseMIMETypes": ["text/csv"],
                "SupportedTransformInstanceTypes": ["ml.c5.xlarge"],
            },
        )
        mlflow.set_tags(
            {
                "training_type": "automatic_model_tuning",
                "sagemaker.tuning_job_name": tuning_job_name,
                "sagemaker.training_job_name": best_training_job_name,
                "sagemaker.model_s3_uri": model_s3_uri,
                "mlflow_run_id": active_run.info.run_id,
                "mlflow_model_id": model_info.model_id,
            }
        )

    print()
    print(f"Tuning job: {tuning_job_name}")
    print(f"Best training job: {best_training_job_name}")
    print(f"Best validation AUC: {validation_auc:.4f}")
    print(f"Best hyperparameters: {best_parameters}")
    print(f"Model: {model_s3_uri}")
    print(f"MLflow run: {mlflow_run_id}")
    print(f"MLflow App: {resources.mlflow_app_arn}")
    print("Next: Compare the AMT run in MLflow and register the selected model")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tune SageMaker built-in XGBoost with Automatic Model Tuning."
    )
    parser.add_argument(
        "--instance-type",
        default=DEFAULT_INSTANCE_TYPE,
        help="SageMaker Training instance. Default: %(default)s",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=30,
        help="Maximum number of Training Jobs. Default: %(default)s",
    )
    parser.add_argument(
        "--max-parallel-jobs",
        type=int,
        default=1,
        help="Maximum parallel Training Jobs. Default: %(default)s",
    )
    args = parser.parse_args()

    run_tuning(
        load_aws_resources(),
        args.instance_type,
        args.max_jobs,
        args.max_parallel_jobs,
    )


if __name__ == "__main__":
    main()
