"""Step 4: conditional SageMaker Model Registry registration."""

from __future__ import annotations

from sagemaker.core import image_uris
from sagemaker.core.model_metrics import MetricsSource, ModelMetrics
from sagemaker.core.workflow.conditions import ConditionGreaterThanOrEqualTo
from sagemaker.core.workflow.execution_variables import ExecutionVariables
from sagemaker.core.workflow.functions import Join, JsonGet
from sagemaker.core.workflow.pipeline_context import PipelineSession
from sagemaker.mlops.workflow import ConditionStep, ModelStep, TrainingStep
from sagemaker.serve.model_builder import ModelBuilder

from sagemaker_xgboost_pipeline.config import XGBOOST_VERSION, Resources
from sagemaker_xgboost_pipeline.parameters import PipelineParameters
from sagemaker_xgboost_pipeline.step_evaluate import EvaluationResult


# Step 4: Register the model when test AUC meets MinAuc, as PendingManualApproval.
# ModelStep does not run a new container.
# It bundles the model artifact and inference image into a Model Package.
def create_step(
    resources: Resources,
    session: PipelineSession,
    parameters: PipelineParameters,
    evaluation: EvaluationResult,
    training_step: TrainingStep,
) -> ConditionStep:
    inference_image = str(
        image_uris.retrieve(
            framework="xgboost",
            region=resources.region,
            version=XGBOOST_VERSION,
            image_scope="inference",
        )
    )
    model = ModelBuilder(
        role_arn=resources.execution_role_arn,
        sagemaker_session=session,
        image_uri=inference_image,
        s3_model_data_url=training_step.properties.ModelArtifacts.S3ModelArtifacts,
        content_type="text/csv",
        accept_type="text/csv",
        dependencies={},
    )
    register_step = ModelStep(
        name="RegisterModel",
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
                            "evaluation.json",
                        ],
                    ),
                )
            ),
            customer_metadata_properties={
                "pipeline_execution_id": ExecutionVariables.PIPELINE_EXECUTION_ID,
                "model_s3_uri": training_step.properties.ModelArtifacts.S3ModelArtifacts,
            },
        ),
    )
    return ConditionStep(
        name="CheckQuality",
        conditions=[
            ConditionGreaterThanOrEqualTo(
                left=JsonGet(
                    step_name=evaluation.step.name,
                    property_file=evaluation.report,
                    json_path="binary_classification_metrics.auc.value",
                ),
                right=parameters.min_auc,
            )
        ],
        if_steps=[register_step],
        else_steps=[],
    )
