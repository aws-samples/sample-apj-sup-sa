"""Step 1: Redshift Data API UNLOAD."""

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
from sagemaker_xgboost_pipeline.step_common import processing_output


@dataclass(frozen=True)
class ExportResult:
    step: ProcessingStep
    summary: PropertyFile


# Step 1: UNLOAD the Redshift source tables to an S3 prefix unique to each execution.
# The next step reads raw_prefix from this summary, so the data snapshot used is reproducible.
def create_step(
    resources: Resources,
    session: PipelineSession,
    code_dir: Path,
    parameters: PipelineParameters,
    execution_prefix: Join,
    raw_prefix: Join,
) -> ExportResult:
    processor = ScriptProcessor(
        role=resources.execution_role_arn,
        image_uri=resources.processing_image_uri,
        command=["python3"],
        instance_count=1,
        instance_type="ml.m5.xlarge",
        sagemaker_session=session,
        base_job_name="xgboost-lab-pipeline-export",
    )
    step_args = processor.run(
        code=str(code_dir / "export_redshift.py"),
        arguments=[
            "--workgroup-name",
            resources.redshift_workgroup_name,
            "--database",
            resources.redshift_database,
            "--unload-role-arn",
            resources.redshift_unload_role_arn,
            "--as-of-date",
            parameters.as_of_date,
            "--label-maturity-date",
            parameters.label_maturity_date,
            "--raw-prefix",
            raw_prefix,
        ],
        outputs=[
            processing_output(
                "export",
                Join(on="/", values=[execution_prefix, "export"]),
                "/opt/ml/processing/output/export",
            )
        ],
    )
    summary = PropertyFile(name="ExportSummary", output_name="export", path="export-summary.json")
    return ExportResult(
        step=ProcessingStep(name="ExportRedshift", step_args=step_args, property_files=[summary]),
        summary=summary,
    )
