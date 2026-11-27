"""Pipeline execution parameters shared by the individual Step definitions."""

from __future__ import annotations

from dataclasses import dataclass

from sagemaker.core.workflow.parameters import ParameterFloat, ParameterString


@dataclass(frozen=True)
class PipelineParameters:
    """Values that can change on each execution without changing the Pipeline code."""

    input_data_s3_uri: ParameterString
    min_auc: ParameterFloat

    @classmethod
    def create(cls, input_data_s3_uri: str) -> PipelineParameters:
        return cls(
            input_data_s3_uri=ParameterString("InputDataS3Uri", default_value=input_data_s3_uri),
            min_auc=ParameterFloat("MinAuc", default_value=0.70),
        )

    def all(self) -> list[object]:
        return [self.input_data_s3_uri, self.min_auc]
