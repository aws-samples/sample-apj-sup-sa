"""Step 4: OOT candidate versus champion evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sagemaker.core.processing import ScriptProcessor
from sagemaker.core.workflow.functions import Join
from sagemaker.core.workflow.pipeline_context import PipelineSession
from sagemaker.core.workflow.properties import PropertyFile
from sagemaker.mlops.workflow import ProcessingStep

from sagemaker_xgboost_pipeline.config import Resources
from sagemaker_xgboost_pipeline.parameters import PipelineParameters
from sagemaker_xgboost_pipeline.step_common import processing_input, processing_output
from sagemaker_xgboost_pipeline.step_tuning import TuningResult


@dataclass(frozen=True)
class EvaluationResult:
    step: ProcessingStep
    report: PropertyFile


# Step 4: Compare the candidate with the currently deployed champion on the same OOT test set.
# The `passes` field in evaluation.json decides whether the next step registers the model.
def create_step(
    resources: Resources,
    session: PipelineSession,
    code_dir: Path,
    parameters: PipelineParameters,
    execution_prefix: Join,
    prepare_step: ProcessingStep,
    tuning: TuningResult,
) -> EvaluationResult:
    processor = ScriptProcessor(
        role=resources.execution_role_arn,
        image_uri=resources.processing_image_uri,
        command=["python3"],
        instance_count=1,
        instance_type="ml.m5.xlarge",
        sagemaker_session=session,
        base_job_name="xgboost-lab-pipeline-evaluate",
    )
    step_args = processor.run(
        code=str(code_dir / "evaluate.py"),
        inputs=[
            processing_input(
                "oot",
                prepare_step.properties.ProcessingOutputConfig.Outputs["oot"].S3Output.S3Uri,
                "/opt/ml/processing/oot",
            ),
            processing_input(
                "candidate",
                tuning.candidate_model_uri,
                "/opt/ml/processing/candidate",
            ),
        ],
        arguments=[
            "--production-model-package-arn",
            parameters.production_model_package_arn,
            "--min-auc-delta",
            parameters.min_auc_delta,
            "--max-brier-regression",
            parameters.max_brier_regression,
        ],
        outputs=[
            processing_output(
                "evaluation",
                Join(on="/", values=[execution_prefix, "evaluation"]),
                "/opt/ml/processing/output/evaluation",
            )
        ],
    )
    report = PropertyFile(
        name="EvaluationReport",
        output_name="evaluation",
        path="evaluation.json",
    )
    return EvaluationResult(
        step=ProcessingStep(name="EvaluateCandidate", step_args=step_args, property_files=[report]),
        report=report,
    )
