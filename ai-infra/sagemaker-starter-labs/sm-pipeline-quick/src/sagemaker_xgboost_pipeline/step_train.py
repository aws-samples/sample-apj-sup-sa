"""Step 2: one XGBoost Training Job with managed MLflow tracking."""

from __future__ import annotations

from pathlib import Path

from sagemaker.core import image_uris
from sagemaker.core.shapes import (
    Channel,
    DataSource,
    OutputDataConfig,
    S3DataSource,
    StoppingCondition,
)
from sagemaker.core.training.configs import Compute, SourceCode
from sagemaker.core.workflow.execution_variables import ExecutionVariables
from sagemaker.core.workflow.pipeline_context import PipelineSession
from sagemaker.mlops.workflow import ProcessingStep, TrainingStep
from sagemaker.train.model_trainer import ModelTrainer

from sagemaker_xgboost_pipeline.config import (
    MLFLOW_EXPERIMENT_NAME,
    PIPELINE_PREFIX,
    XGBOOST_VERSION,
    Resources,
)


def create_step(
    resources: Resources,
    session: PipelineSession,
    code_dir: Path,
    prepare_step: ProcessingStep,
) -> TrainingStep:
    trainer = ModelTrainer(
        sagemaker_session=session,
        role=resources.execution_role_arn,
        training_image=str(
            image_uris.retrieve(
                framework="xgboost",
                region=resources.region,
                version=XGBOOST_VERSION,
                image_scope="training",
            )
        ),
        source_code=SourceCode(
            source_dir=str(code_dir),
            entry_script="train.py",
            requirements="requirements-training.txt",
        ),
        base_job_name="xgboost-lab-pipeline-train",
        compute=Compute(instance_type="ml.c6i.xlarge", instance_count=1),
        stopping_condition=StoppingCondition(max_runtime_in_seconds=1800),
        output_data_config=OutputDataConfig(
            s3_output_path=f"s3://{resources.bucket}/{PIPELINE_PREFIX}/training"
        ),
        environment={
            "MLFLOW_TRACKING_URI": resources.mlflow_app_arn,
            "MLFLOW_EXPERIMENT_NAME": MLFLOW_EXPERIMENT_NAME,
            "PIPELINE_EXECUTION_ID": ExecutionVariables.PIPELINE_EXECUTION_ID,
        },
        hyperparameters={
            "num_boost_round": 100,
            "early_stopping_rounds": 10,
            "seed": 42,
        },
    )
    channels = [
        Channel(
            channel_name=name,
            content_type="text/csv" if name != "metadata" else "application/json",
            data_source=DataSource(
                s3_data_source=S3DataSource(
                    s3_data_type="S3Prefix",
                    s3_uri=prepare_step.properties.ProcessingOutputConfig.Outputs[
                        name
                    ].S3Output.S3Uri,
                )
            ),
        )
        for name in ("train", "validation", "metadata")
    ]
    step_args = trainer.train(input_data_config=channels)
    return TrainingStep(name="TrainModel", step_args=step_args)
