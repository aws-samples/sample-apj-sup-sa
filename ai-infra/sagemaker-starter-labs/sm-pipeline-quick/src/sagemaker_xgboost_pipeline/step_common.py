"""Small helpers shared by SageMaker Processing Steps."""

from __future__ import annotations

from typing import Any

from sagemaker.core.shapes import (
    ProcessingInput,
    ProcessingOutput,
    ProcessingS3Input,
    ProcessingS3Output,
)


def processing_output(name: str, destination: Any, local_path: str) -> ProcessingOutput:
    return ProcessingOutput(
        output_name=name,
        s3_output=ProcessingS3Output(
            s3_uri=destination,
            s3_upload_mode="EndOfJob",
            local_path=local_path,
        ),
    )


def processing_input(name: str, source: Any, local_path: str) -> ProcessingInput:
    return ProcessingInput(
        input_name=name,
        s3_input=ProcessingS3Input(
            s3_uri=source,
            s3_data_type="S3Prefix",
            s3_input_mode="File",
            local_path=local_path,
        ),
    )
