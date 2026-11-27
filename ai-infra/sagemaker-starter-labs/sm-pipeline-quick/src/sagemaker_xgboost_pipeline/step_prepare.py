"""Step 1: validate UCI input and create reproducible train, validation, and test splits."""

from __future__ import annotations

from pathlib import Path

from sagemaker.core.processing import ScriptProcessor
from sagemaker.core.workflow.functions import Join
from sagemaker.core.workflow.pipeline_context import PipelineSession
from sagemaker.mlops.workflow import ProcessingStep

from sagemaker_xgboost_pipeline.config import Resources
from sagemaker_xgboost_pipeline.parameters import PipelineParameters
from sagemaker_xgboost_pipeline.step_common import processing_input, processing_output


# Step 1: SageMaker downloads the input snapshot; prepare.py writes three CSV splits.
# Train and validation feed Step 2. Test feeds Step 3.
def create_step(
    resources: Resources,
    session: PipelineSession,
    code_dir: Path,
    parameters: PipelineParameters,
    execution_prefix: Join,
) -> ProcessingStep:
    processor = ScriptProcessor(
        role=resources.execution_role_arn,
        image_uri=resources.processing_image_uri,
        command=["python3"],
        instance_count=1,
        instance_type="ml.m5.xlarge",
        max_runtime_in_seconds=1200,
        sagemaker_session=session,
        base_job_name="xgboost-lab-pipeline-prepare",
    )
    step_args = processor.run(
        code=str(code_dir / "prepare.py"),
        inputs=[
            processing_input("data", parameters.input_data_s3_uri, "/opt/ml/processing/input/data")
        ],
        outputs=[
            processing_output(
                "train",
                Join(on="/", values=[execution_prefix, "prepared", "train"]),
                "/opt/ml/processing/output/train",
            ),
            processing_output(
                "validation",
                Join(on="/", values=[execution_prefix, "prepared", "validation"]),
                "/opt/ml/processing/output/validation",
            ),
            processing_output(
                "test",
                Join(on="/", values=[execution_prefix, "prepared", "test"]),
                "/opt/ml/processing/output/test",
            ),
            processing_output(
                "metadata",
                Join(on="/", values=[execution_prefix, "prepared", "metadata"]),
                "/opt/ml/processing/output/metadata",
            ),
        ],
    )
    return ProcessingStep(name="PrepareData", step_args=step_args)
