"""Pipeline execution parameters shared by the individual Step definitions."""

from __future__ import annotations

from dataclasses import dataclass

from sagemaker.core.workflow.parameters import ParameterString


@dataclass(frozen=True)
class PipelineParameters:
    """Values that can change on each execution without changing the Pipeline code."""

    as_of_date: ParameterString
    label_maturity_date: ParameterString
    validation_start: ParameterString
    oot_start: ParameterString
    production_model_package_arn: ParameterString
    min_auc_delta: ParameterString
    max_brier_regression: ParameterString

    @classmethod
    def create(cls) -> PipelineParameters:
        return cls(
            as_of_date=ParameterString("AsOfDate", default_value="2026-06-30"),
            label_maturity_date=ParameterString("LabelMaturityDate", default_value="2026-03-31"),
            validation_start=ParameterString("ValidationStart", default_value="2025-10-01"),
            oot_start=ParameterString("OotStart", default_value="2026-01-01"),
            production_model_package_arn=ParameterString(
                "ProductionModelPackageArn",
                default_value="",
            ),
            # Processing container arguments are strings; evaluate.py parses these as floats.
            min_auc_delta=ParameterString("MinAucDelta", default_value="0.01"),
            max_brier_regression=ParameterString("MaxBrierRegression", default_value="0.005"),
        )

    def all(self) -> list[object]:
        return [
            self.as_of_date,
            self.label_maturity_date,
            self.validation_start,
            self.oot_start,
            self.production_model_package_arn,
            self.min_auc_delta,
            self.max_brier_regression,
        ]
