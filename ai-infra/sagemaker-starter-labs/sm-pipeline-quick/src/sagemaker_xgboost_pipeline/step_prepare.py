"""Step 2: pandas feature preparation."""

from __future__ import annotations

from pathlib import Path

from sagemaker.core.processing import ScriptProcessor
from sagemaker.core.workflow.functions import Join, JsonGet
from sagemaker.core.workflow.pipeline_context import PipelineSession
from sagemaker.mlops.workflow import ProcessingStep

from sagemaker_xgboost_pipeline.config import Resources
from sagemaker_xgboost_pipeline.parameters import PipelineParameters
from sagemaker_xgboost_pipeline.step_common import processing_output
from sagemaker_xgboost_pipeline.step_export import ExportResult


# Step 2: Read Parquet with pandas, then join, handle missing values, and split by time.
# This step's train/validation/OOT S3 outputs are the inputs to Steps 3 and 4.
def create_step(
    resources: Resources,
    session: PipelineSession,
    code_dir: Path,
    parameters: PipelineParameters,
    execution_prefix: Join,
    export: ExportResult,
) -> ProcessingStep:
    processor = ScriptProcessor(
        role=resources.execution_role_arn,
        image_uri=resources.processing_image_uri,
        command=["python3"],
        instance_count=1,
        instance_type="ml.m5.xlarge",
        sagemaker_session=session,
        base_job_name="xgboost-lab-pipeline-prepare",
    )
    raw_prefix = JsonGet(
        step_name=export.step.name,
        property_file=export.summary,
        json_path="raw_prefix",
    )
    step_args = processor.run(
        code=str(code_dir / "prepare.py"),
        arguments=[
            "--raw-prefix",
            raw_prefix,
            "--validation-start",
            parameters.validation_start,
            "--oot-start",
            parameters.oot_start,
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
                "oot",
                Join(on="/", values=[execution_prefix, "prepared", "oot"]),
                "/opt/ml/processing/output/oot",
            ),
            processing_output(
                "metadata",
                Join(on="/", values=[execution_prefix, "prepared", "metadata"]),
                "/opt/ml/processing/output/metadata",
            ),
        ],
    )
    return ProcessingStep(name="PrepareFeatures", step_args=step_args)
