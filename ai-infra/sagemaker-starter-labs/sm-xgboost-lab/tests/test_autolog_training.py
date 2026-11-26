from __future__ import annotations

import json

import numpy as np

from sagemaker_xgboost_lab.autolog import load_channel, load_feature_names
from sagemaker_xgboost_lab.config import AUTOLOG_XGBOOST_VERSION


def test_load_channel_splits_label_first_csv(tmp_path) -> None:
    feature_names = ["LIMIT_BAL", "PAY_0", "BILL_AMT1", "PAY_AMT1"]
    rows = np.array(
        [
            [1.0, 3.1, -2.0, 0.5, 1.2],
            [0.0, -1.4, 4.2, -0.7, 2.0],
        ],
        dtype=np.float32,
    )
    np.savetxt(tmp_path / "train.csv", rows, delimiter=",")

    matrix = load_channel(str(tmp_path), feature_names)

    np.testing.assert_array_equal(matrix.get_label(), rows[:, 0])
    assert matrix.num_row() == 2
    assert matrix.num_col() == 4
    assert matrix.feature_names == feature_names


def test_load_feature_names_uses_data_summary(tmp_path) -> None:
    summary_path = tmp_path / "data-summary.json"
    summary_path.write_text(
        json.dumps({"feature_names": ["income", "debt_ratio", "payment_history"]}),
        encoding="utf-8",
    )

    feature_names, loaded_path = load_feature_names(str(tmp_path))

    assert feature_names == ["income", "debt_ratio", "payment_history"]
    assert loaded_path == summary_path


def test_autolog_uses_an_mlflow_compatible_xgboost_image() -> None:
    assert AUTOLOG_XGBOOST_VERSION == "3.0-5"
