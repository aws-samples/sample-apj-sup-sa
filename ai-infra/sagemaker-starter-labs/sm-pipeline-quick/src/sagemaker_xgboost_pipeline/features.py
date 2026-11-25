"""Pure pandas feature preparation used by the SageMaker Processing step."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FeatureTransform:
    """The train-fitted values used to transform validation and OOT data consistently."""

    numeric_medians: dict[str, float]
    categorical_values: dict[str, list[str]]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_feature_frame(
    applications: pd.DataFrame,
    seller_profiles: pd.DataFrame,
    settlements: pd.DataFrame,
) -> pd.DataFrame:
    """Join three assumed Redshift extracts and create point-in-time-safe simple features."""

    applications = applications.copy()
    seller_profiles = seller_profiles.copy()
    settlements = settlements.copy()
    applications["observation_date"] = pd.to_datetime(applications["observation_date"])
    settlements["settlement_date"] = pd.to_datetime(settlements["settlement_date"])

    # A profile must have one row per seller; otherwise this join silently duplicates applications.
    base = applications.merge(seller_profiles, on="seller_id", how="left", validate="m:1")
    history = applications[
        ["application_id", "seller_id", "observation_date", "advance_amount"]
    ].merge(settlements, on="seller_id", how="left")
    lookback_start = pd.to_datetime(
        history["observation_date"].astype("int64") - 30 * 24 * 60 * 60 * 1_000_000_000
    )
    history = history[
        (history["settlement_date"] <= history["observation_date"])
        & (history["settlement_date"] > lookback_start)
    ]

    settlement_30d = (
        history.groupby("application_id", as_index=False)
        .agg(
            settled_amount_30d=("settled_amount", "sum"),
            scheduled_amount_30d=("scheduled_amount", "sum"),
            settlement_count_30d=("settlement_date", "count"),
            late_days_mean_30d=("days_late", "mean"),
        )
        .copy()
    )
    features = base.merge(settlement_30d, on="application_id", how="left")

    # Ratio of money actually settled to money scheduled in the preceding 30 days.
    # It is a small, intuitive signal of settlement fulfillment, not a business decision by itself.
    denominator = features["scheduled_amount_30d"].replace(0, np.nan)
    features["settlement_ratio"] = features["settled_amount_30d"] / denominator
    features["has_settlement_history"] = features["settlement_count_30d"].notna().astype(int)
    return features


def split_by_time(
    features: pd.DataFrame,
    validation_start: str,
    oot_start: str,
    label_column: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split past, validation, and out-of-time (OOT) periods without random sampling."""

    validation_start_at = pd.Timestamp(validation_start)
    oot_start_at = pd.Timestamp(oot_start)
    if validation_start_at >= oot_start_at:
        raise ValueError("validation_start must be earlier than oot_start.")

    train = features[features["observation_date"] < validation_start_at].copy()
    validation = features[
        (features["observation_date"] >= validation_start_at)
        & (features["observation_date"] < oot_start_at)
    ].copy()
    oot = features[features["observation_date"] >= oot_start_at].copy()
    for name, frame in {"train": train, "validation": validation, "oot": oot}.items():
        if frame.empty:
            raise ValueError(f"The {name} time split must contain at least one row.")
        labels = pd.to_numeric(frame[label_column], errors="raise")
        if set(labels.unique()) != {0, 1}:
            raise ValueError(f"The {name} time split must contain labels 0 and 1.")
    return train, validation, oot


def model_feature_columns(features: pd.DataFrame, label_column: str) -> list[str]:
    """Keep only columns available at application time; sensitive columns stay out of inputs."""

    excluded = {
        "application_id",
        "seller_id",
        "observation_date",
        label_column,
        "sensitive_age_band",
        "sensitive_gender",
    }
    return [column for column in features.columns if column not in excluded]


def fit_transformer(train: pd.DataFrame, feature_columns: list[str]) -> FeatureTransform:
    """Fit median imputation and categorical mappings on training data only."""

    numeric = train[feature_columns].select_dtypes(include=["number", "bool"]).columns.tolist()
    categorical = [column for column in feature_columns if column not in numeric]
    return FeatureTransform(
        numeric_medians={
            column: float(pd.to_numeric(train[column], errors="coerce").median())
            for column in numeric
        },
        categorical_values={
            column: sorted(train[column].dropna().astype(str).unique().tolist())
            for column in categorical
        },
    )


def transform_features(
    frame: pd.DataFrame,
    feature_columns: list[str],
    transformer: FeatureTransform,
) -> pd.DataFrame:
    """Create the numeric matrix consumed by XGBoost using the train-fitted transform."""

    transformed = pd.DataFrame(index=frame.index)
    for column in feature_columns:
        if column in transformer.numeric_medians:
            transformed[column] = (
                pd.to_numeric(frame[column], errors="coerce")
                .fillna(transformer.numeric_medians[column])
                .astype(float)
            )
        else:
            categories = {
                value: index for index, value in enumerate(transformer.categorical_values[column])
            }
            transformed[column] = (
                frame[column].fillna("__missing__").astype(str).map(categories).fillna(-1)
            )
    return transformed.astype(float)


def model_frame(
    frame: pd.DataFrame,
    label_column: str,
    feature_columns: list[str],
    transformer: FeatureTransform,
) -> pd.DataFrame:
    """Return SageMaker XGBoost's simple label-first CSV layout."""

    labels = pd.to_numeric(frame[label_column], errors="raise").astype(int).rename(label_column)
    return pd.concat([labels, transform_features(frame, feature_columns, transformer)], axis=1)
