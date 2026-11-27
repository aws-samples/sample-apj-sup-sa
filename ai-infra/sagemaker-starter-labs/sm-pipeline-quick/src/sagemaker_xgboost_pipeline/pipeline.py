"""The short, high-level SageMaker Pipeline composition.

Start here. Each function call below creates one SageMaker Pipeline Step.
Read the relevant `step_*.py` file only after this four-step flow is clear.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import boto3
from sagemaker.core.workflow.execution_variables import ExecutionVariables
from sagemaker.core.workflow.functions import Join
from sagemaker.core.workflow.pipeline_context import PipelineSession
from sagemaker.mlops.workflow import Pipeline

from sagemaker_xgboost_pipeline.config import (
    PIPELINE_NAME,
    PIPELINE_PREFIX,
    Resources,
)
from sagemaker_xgboost_pipeline.parameters import PipelineParameters
from sagemaker_xgboost_pipeline.step_evaluate import create_step as create_evaluation_step
from sagemaker_xgboost_pipeline.step_prepare import create_step as create_prepare_step
from sagemaker_xgboost_pipeline.step_register import create_step as create_registration_gate
from sagemaker_xgboost_pipeline.step_train import create_step as create_training_step


def build_pipeline(resources: Resources) -> Pipeline:
    """Connect the four Step definitions into one SageMaker Pipeline."""

    code_dir = Path(__file__).resolve().parent
    session = PipelineSession(
        boto_session=boto3.Session(region_name=resources.region),
        default_bucket=resources.bucket,
        default_bucket_prefix=PIPELINE_PREFIX,
    )
    parameters = PipelineParameters.create(resources.input_data_s3_uri)
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

    # 1. Validate the UCI input and split it into train, validation, and test data.
    prepare = create_prepare_step(
        resources,
        session,
        code_dir,
        parameters,
        execution_prefix,
    )

    # 2. Train one XGBoost model and record training metrics in managed MLflow.
    training = create_training_step(resources, session, code_dir, prepare)

    # 3. Evaluate the model on the test split.
    evaluation = create_evaluation_step(
        resources,
        session,
        code_dir,
        execution_prefix,
        prepare,
        training,
    )

    # 4. Compare test AUC with MinAuc and register a passing model.
    registration_gate = create_registration_gate(
        resources, session, parameters, evaluation, training
    )

    return Pipeline(
        name=PIPELINE_NAME,
        parameters=parameters.all(),
        steps=[prepare, training, evaluation.step, registration_gate],
        sagemaker_session=session,
        pipeline_experiment_config=None,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upsert", action="store_true")
    parser.add_argument("--definition", type=Path, default=Path("pipeline-definition.json"))
    args = parser.parse_args()

    resources = Resources.from_environment()
    pipeline = build_pipeline(resources)
    args.definition.write_text(pipeline.definition(), encoding="utf-8")
    print(f"Wrote Pipeline definition: {args.definition}")
    if args.upsert:
        pipeline.upsert(role_arn=resources.execution_role_arn)
        print(f"Upserted Pipeline: {pipeline.name}")


if __name__ == "__main__":
    main()
