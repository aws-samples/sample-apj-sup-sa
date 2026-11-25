"""The short, high-level SageMaker Pipeline composition.

Start here. Each function call below creates one SageMaker Pipeline Step.
Read the relevant `step_*.py` file only after this five-step flow is clear.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import boto3
from sagemaker.core.shapes import MlflowConfig
from sagemaker.core.workflow.execution_variables import ExecutionVariables
from sagemaker.core.workflow.functions import Join
from sagemaker.core.workflow.pipeline_context import PipelineSession
from sagemaker.mlops.workflow import Pipeline

from sagemaker_xgboost_pipeline.config import (
    MLFLOW_EXPERIMENT_NAME,
    PIPELINE_NAME,
    PIPELINE_PREFIX,
    Resources,
)
from sagemaker_xgboost_pipeline.parameters import PipelineParameters
from sagemaker_xgboost_pipeline.step_evaluate import create_step as create_evaluation_step
from sagemaker_xgboost_pipeline.step_export import create_step as create_export_step
from sagemaker_xgboost_pipeline.step_prepare import create_step as create_prepare_step
from sagemaker_xgboost_pipeline.step_register import create_step as create_registration_gate
from sagemaker_xgboost_pipeline.step_tuning import create_step as create_tuning_step


def build_pipeline(resources: Resources) -> Pipeline:
    """Connect the five Step definitions into one SageMaker Pipeline."""

    code_dir = Path(__file__).resolve().parent
    session = PipelineSession(
        boto_session=boto3.Session(region_name=resources.region),
        default_bucket=resources.bucket,
        default_bucket_prefix=PIPELINE_PREFIX,
    )
    parameters = PipelineParameters.create()
    execution_prefix = Join(
        on="/",
        values=[
            "s3:/",
            resources.bucket,
            PIPELINE_PREFIX,
            "executions",
            ExecutionVariables.PIPELINE_EXECUTION_ID,
        ],
    )

    # 1. Export a reproducible Redshift snapshot.
    export = create_export_step(
        resources,
        session,
        code_dir,
        parameters,
        execution_prefix,
        Join(on="/", values=[execution_prefix, "raw"]),
    )

    # 2. Join, fill missing values, and create train / validation / OOT data.
    prepare = create_prepare_step(
        resources,
        session,
        code_dir,
        parameters,
        execution_prefix,
        export,
    )

    # 3. Tune script-mode XGBoost jobs and record each trial in managed MLflow.
    tuning = create_tuning_step(resources, session, code_dir, parameters, prepare)

    # 4. Compare the best candidate and deployed champion on the same OOT test.
    evaluation = create_evaluation_step(
        resources,
        session,
        code_dir,
        parameters,
        execution_prefix,
        prepare,
        tuning,
    )

    # 5. Register only a candidate that passes the OOT quality gate.
    registration_gate = create_registration_gate(resources, session, evaluation, tuning)

    return Pipeline(
        name=PIPELINE_NAME,
        parameters=parameters.all(),
        steps=[export.step, prepare, tuning.step, evaluation.step, registration_gate],
        sagemaker_session=session,
        mlflow_config=MlflowConfig(
            mlflow_resource_arn=resources.mlflow_app_arn,
            mlflow_experiment_name=MLFLOW_EXPERIMENT_NAME,
        ),
        pipeline_experiment_config=None,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upsert", action="store_true")
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--definition", type=Path, default=Path("pipeline-definition.json"))
    args = parser.parse_args()

    resources = Resources.from_environment()
    pipeline = build_pipeline(resources)
    args.definition.write_text(pipeline.definition(), encoding="utf-8")
    print(f"Wrote Pipeline definition: {args.definition}")
    if args.upsert:
        pipeline.upsert(role_arn=resources.execution_role_arn)
        print(f"Upserted Pipeline: {pipeline.name}")
    if args.start:
        execution = pipeline.start()
        print(f"Started Pipeline: {execution.arn}")


if __name__ == "__main__":
    main()
