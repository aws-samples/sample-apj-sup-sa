"""Step 5: conditional SageMaker Model Registry registration."""

from __future__ import annotations

from sagemaker.core import image_uris
from sagemaker.core.model_metrics import MetricsSource, ModelMetrics
from sagemaker.core.workflow.conditions import ConditionEquals
from sagemaker.core.workflow.execution_variables import ExecutionVariables
from sagemaker.core.workflow.functions import Join, JsonGet
from sagemaker.core.workflow.pipeline_context import PipelineSession
from sagemaker.mlops.workflow import ConditionStep, ModelStep
from sagemaker.serve.model_builder import ModelBuilder

from sagemaker_xgboost_pipeline.config import Resources
from sagemaker_xgboost_pipeline.step_evaluate import EvaluationResult
from sagemaker_xgboost_pipeline.step_tuning import TuningResult


# Step 5: Register only candidates that pass the OOT quality gate, as PendingManualApproval.
# ModelStep does not run a new container.
# It bundles the model artifact and inference image into a Model Package.
def create_step(
    resources: Resources,
    session: PipelineSession,
    evaluation: EvaluationResult,
    tuning: TuningResult,
) -> ConditionStep:
    inference_image = str(
        image_uris.retrieve(
            framework="xgboost",
            region=resources.region,
            version="3.0-5",
            image_scope="inference",
        )
    )
    model = ModelBuilder(
        role_arn=resources.execution_role_arn,
        sagemaker_session=session,
        image_uri=inference_image,
        s3_model_data_url=tuning.candidate_model_uri,
        content_type="text/csv",
        accept_type="text/csv",
        dependencies={},
    )
    register_step = ModelStep(
        name="RegisterCandidate",
        step_args=model.register(
            model_package_group_name=resources.model_package_group_name,
            content_types=["text/csv"],
            response_types=["text/csv"],
            inference_instances=["ml.c6i.xlarge"],
            transform_instances=["ml.c6i.xlarge"],
            approval_status="PendingManualApproval",
            model_metrics=ModelMetrics(
                model_statistics=MetricsSource(
                    content_type="application/json",
                    s3_uri=Join(
                        on="/",
                        values=[
                            evaluation.step.properties.ProcessingOutputConfig.Outputs[
                                "evaluation"
                            ].S3Output.S3Uri,
                            "model-quality.json",
                        ],
                    ),
                )
            ),
            customer_metadata_properties={
                "pipeline_execution_id": ExecutionVariables.PIPELINE_EXECUTION_ID,
                "candidate_model_s3_uri": tuning.candidate_model_uri,
            },
        ),
    )
    return ConditionStep(
        name="RegisterOnlyIfBetter",
        conditions=[
            ConditionEquals(
                left=JsonGet(
                    step_name=evaluation.step.name,
                    property_file=evaluation.report,
                    json_path="decision.passes",
                ),
                right=True,
            )
        ],
        if_steps=[register_step],
        else_steps=[],
    )
