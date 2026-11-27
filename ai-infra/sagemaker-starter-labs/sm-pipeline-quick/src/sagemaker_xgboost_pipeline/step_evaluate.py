"""Step 3: evaluate the trained model on the held-out test split."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sagemaker.core.processing import ScriptProcessor
from sagemaker.core.workflow.functions import Join
from sagemaker.core.workflow.pipeline_context import PipelineSession
from sagemaker.core.workflow.properties import PropertyFile
from sagemaker.mlops.workflow import ProcessingStep, TrainingStep

from sagemaker_xgboost_pipeline.config import Resources
from sagemaker_xgboost_pipeline.step_common import processing_input, processing_output


@dataclass(frozen=True)
class EvaluationResult:
    step: ProcessingStep
    report: PropertyFile


# Step 3: Test data comes from PrepareData; the model comes from TrainModel.
# Step 4 reads the AUC from evaluation.json.
def create_step(
    resources: Resources,
    session: PipelineSession,
    code_dir: Path,
    execution_prefix: Join,
    prepare_step: ProcessingStep,
    training_step: TrainingStep,
) -> EvaluationResult:
    processor = ScriptProcessor(
        role=resources.execution_role_arn,
        image_uri=resources.processing_image_uri,
        command=["python3"],
        instance_count=1,
        instance_type="ml.m5.xlarge",
        max_runtime_in_seconds=1200,
        sagemaker_session=session,
        base_job_name="xgboost-lab-pipeline-evaluate",
    )
    step_args = processor.run(
        code=str(code_dir / "evaluate.py"),
        inputs=[
            processing_input(
                "test",
                prepare_step.properties.ProcessingOutputConfig.Outputs["test"].S3Output.S3Uri,
                "/opt/ml/processing/test",
            ),
            processing_input(
                "model",
                training_step.properties.ModelArtifacts.S3ModelArtifacts,
                "/opt/ml/processing/model",
            ),
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
        step=ProcessingStep(name="EvaluateModel", step_args=step_args, property_files=[report]),
        report=report,
    )
