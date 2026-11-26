"""Step 3: SageMaker Automatic Model Tuning (AMT)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sagemaker.core import image_uris
from sagemaker.core.parameter import ContinuousParameter, IntegerParameter
from sagemaker.core.shapes import (
    Channel,
    DataSource,
    OutputDataConfig,
    S3DataSource,
    StoppingCondition,
)
from sagemaker.core.training.configs import Compute, SourceCode
from sagemaker.core.workflow.execution_variables import ExecutionVariables
from sagemaker.core.workflow.functions import Join
from sagemaker.core.workflow.pipeline_context import PipelineSession
from sagemaker.mlops.workflow import ProcessingStep, TuningStep
from sagemaker.train.model_trainer import ModelTrainer
from sagemaker.train.tuner import HyperparameterTuner

from sagemaker_xgboost_pipeline.config import (
    MLFLOW_EXPERIMENT_NAME,
    PIPELINE_PREFIX,
    Resources,
)
from sagemaker_xgboost_pipeline.parameters import PipelineParameters

MAX_TUNING_JOBS = 8
MAX_PARALLEL_JOBS = 2


@dataclass(frozen=True)
class TuningResult:
    step: TuningStep
    candidate_model_uri: Join


# Step 3: AMT runs the same train.py across multiple Training Jobs.
# SDK v3 uploads SourceCode to S3 as a tar.gz and picks the trial with the highest validation-auc.
# Trial counts are constants in this file to fit SDK v3's current ResourceLimits constraints.
def create_step(
    resources: Resources,
    session: PipelineSession,
    code_dir: Path,
    parameters: PipelineParameters,
    prepare_step: ProcessingStep,
) -> TuningResult:
    training_image = str(
        image_uris.retrieve(
            framework="xgboost",
            region=resources.region,
            version="3.0-5",
            image_scope="training",
        )
    )
    trainer = ModelTrainer(
        sagemaker_session=session,
        role=resources.execution_role_arn,
        training_image=training_image,
        source_code=SourceCode(
            source_dir=str(code_dir),
            entry_script="train.py",
            requirements="requirements-training.txt",
        ),
        base_job_name="xgboost-lab-pipeline-tune",
        compute=Compute(instance_type="ml.c6i.xlarge", instance_count=1),
        stopping_condition=StoppingCondition(max_runtime_in_seconds=3600),
        output_data_config=OutputDataConfig(
            s3_output_path=f"s3://{resources.bucket}/{PIPELINE_PREFIX}/tuning-output"
        ),
        environment={
            "MLFLOW_TRACKING_URI": resources.mlflow_app_arn,
            "MLFLOW_EXPERIMENT_NAME": MLFLOW_EXPERIMENT_NAME,
            "PIPELINE_EXECUTION_ID": ExecutionVariables.PIPELINE_EXECUTION_ID,
            "DATA_SUMMARY_S3_URI": prepare_step.properties.ProcessingOutputConfig.Outputs[
                "metadata"
            ].S3Output.S3Uri,
        },
        hyperparameters={
            "num_boost_round": 300,
            "early_stopping_rounds": 30,
            "seed": 42,
            "colsample_bytree": 0.9,
        },
    )
    tuner = HyperparameterTuner(
        model_trainer=trainer,
        objective_metric_name="validation-auc",
        hyperparameter_ranges={
            "eta": ContinuousParameter(0.03, 0.3, scaling_type="Logarithmic"),
            "max_depth": IntegerParameter(2, 8),
            "min_child_weight": ContinuousParameter(1, 10, scaling_type="Logarithmic"),
            "subsample": ContinuousParameter(0.6, 1.0),
        },
        metric_definitions=[{"Name": "validation-auc", "Regex": r"validation-auc=([0-9.]+)"}],
        strategy="Bayesian",
        objective_type="Maximize",
        max_jobs=MAX_TUNING_JOBS,
        max_parallel_jobs=MAX_PARALLEL_JOBS,
        early_stopping_type="Auto",
        random_seed=42,
    )
    step_args = tuner.tune(
        inputs=[
            Channel(
                channel_name="train",
                data_source=DataSource(
                    s3_data_source=S3DataSource(
                        s3_data_type="S3Prefix",
                        s3_uri=prepare_step.properties.ProcessingOutputConfig.Outputs[
                            "train"
                        ].S3Output.S3Uri,
                    )
                ),
            ),
            Channel(
                channel_name="validation",
                data_source=DataSource(
                    s3_data_source=S3DataSource(
                        s3_data_type="S3Prefix",
                        s3_uri=prepare_step.properties.ProcessingOutputConfig.Outputs[
                            "validation"
                        ].S3Output.S3Uri,
                    )
                ),
            ),
            Channel(
                channel_name="metadata",
                data_source=DataSource(
                    s3_data_source=S3DataSource(
                        s3_data_type="S3Prefix",
                        s3_uri=prepare_step.properties.ProcessingOutputConfig.Outputs[
                            "metadata"
                        ].S3Output.S3Uri,
                    )
                ),
            ),
        ]
    )
    step = TuningStep(name="TrainTuneModel", step_args=step_args)
    return TuningResult(
        step=step,
        candidate_model_uri=step.get_top_model_s3_uri(
            top_k=0,
            s3_bucket=resources.bucket,
            prefix=f"{PIPELINE_PREFIX}/tuning-output",
        ),
    )
