from __future__ import annotations

import pandas as pd

from sagemaker_xgboost_pipeline.features import (
    build_feature_frame,
    fit_transformer,
    model_feature_columns,
    model_frame,
    split_by_time,
)


def test_feature_frame_uses_only_settlements_available_at_observation_time() -> None:
    applications = pd.DataFrame(
        {
            "application_id": [1, 2, 3],
            "seller_id": [10, 10, 20],
            "observation_date": ["2025-01-15", "2025-10-15", "2026-01-15"],
            "advance_amount": [100, 100, 100],
            "requested_limit": [120, 120, 120],
            "default_payment_next_month": [0, 0, 1],
        }
    )
    profiles = pd.DataFrame(
        {
            "seller_id": [10, 20],
            "industry_code": ["retail", "food"],
            "business_age_days": [300, 500],
            "registered_region": ["Seoul", "Busan"],
        }
    )
    settlements = pd.DataFrame(
        {
            "seller_id": [10, 10, 10, 20],
            "settlement_date": ["2025-01-10", "2025-02-01", "2025-10-10", "2026-01-10"],
            "scheduled_amount": [100, 100, 100, 100],
            "settled_amount": [90, 100, 80, 95],
            "days_late": [1, 0, 3, 0],
        }
    )

    features = build_feature_frame(applications, profiles, settlements)

    first_application = features.set_index("application_id").loc[1]
    assert first_application["settlement_ratio"] == 0.9
    assert first_application["settlement_count_30d"] == 1


def test_time_split_and_transform_fit_on_train_only() -> None:
    features = pd.DataFrame(
        {
            "application_id": [1, 2, 3, 4, 5, 6],
            "seller_id": [10, 11, 12, 13, 14, 15],
            "observation_date": pd.to_datetime(
                [
                    "2025-01-01",
                    "2025-02-01",
                    "2025-10-01",
                    "2025-11-01",
                    "2026-01-01",
                    "2026-02-01",
                ]
            ),
            "default_payment_next_month": [0, 1, 0, 1, 0, 1],
            "industry_code": ["retail", "retail", "retail", "food", "new-category", "retail"],
            "settlement_ratio": [0.9, 0.9, 0.8, 0.7, None, 0.6],
        }
    )

    train, validation, oot = split_by_time(
        features,
        "2025-06-01",
        "2025-12-01",
        "default_payment_next_month",
    )
    columns = model_feature_columns(features, "default_payment_next_month")
    transformer = fit_transformer(train, columns)
    oot_model_frame = model_frame(oot, "default_payment_next_month", columns, transformer)

    assert len(train) == len(validation) == len(oot) == 2
    assert oot_model_frame["industry_code"].iloc[0] == -1
    assert oot_model_frame["settlement_ratio"].iloc[0] == 0.9
