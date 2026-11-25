from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from sagemaker_xgboost_lab.data_prep import (
    FEATURE_NAMES,
    load_credit_default_data,
    prepare_training_data,
    stratified_split,
)


def _fetcher(**_: int) -> SimpleNamespace:
    return SimpleNamespace(
        data=SimpleNamespace(
            features=pd.DataFrame(
                {
                    f"X{index}": [
                        index,
                        index + 1,
                        index + 2,
                        index + 3,
                        index + 4,
                        index + 5,
                        index + 6,
                        index + 7,
                    ]
                    for index in range(1, 24)
                }
            ),
            targets=pd.DataFrame({"Y": [0, 0, 0, 0, 1, 1, 1, 1]}),
        )
    )


def test_public_data_is_numeric_binary_data() -> None:
    features, labels = load_credit_default_data(fetcher=_fetcher)

    assert features.columns.tolist() == list(FEATURE_NAMES)
    assert set(labels.unique()) == {0, 1}
    assert labels.name == "label"


def test_split_is_reproducible_and_stratified() -> None:
    features, labels = load_credit_default_data(fetcher=_fetcher)

    first = stratified_split(features, labels, validation_fraction=0.25, random_seed=7)
    second = stratified_split(features, labels, validation_fraction=0.25, random_seed=7)

    assert first.x_train.equals(second.x_train)
    assert first.y_validation.tolist() == second.y_validation.tolist()
    assert set(first.y_train.unique()) == {0, 1}
    assert set(first.y_validation.unique()) == {0, 1}


def test_sagemaker_csv_has_label_first_and_no_header(tmp_path) -> None:
    prepared = prepare_training_data(tmp_path, fetcher=_fetcher)
    first_row = prepared.train_csv.read_text(encoding="utf-8").splitlines()[0].split(",")

    assert len(first_row) == 24
    assert first_row[0] in {"0", "1"}
    assert "LIMIT_BAL" not in prepared.train_csv.read_text(encoding="utf-8")


def test_data_summary_explains_target_and_class_counts(tmp_path) -> None:
    prepared = prepare_training_data(tmp_path, fetcher=_fetcher)
    summary = json.loads(prepared.summary_json.read_text(encoding="utf-8"))

    assert summary["dataset"] == "UCI Default of Credit Card Clients"
    assert summary["target"]["positive_label_meaning"] == "default payment next month"
    assert summary["class_distribution"]["validation"] == {
        "non_default": 1,
        "default": 1,
    }


def test_public_data_requires_the_expected_uci_schema() -> None:
    def wrong_fetcher(**_: int) -> SimpleNamespace:
        dataset = _fetcher()
        dataset.data.features = dataset.data.features.rename(columns={"X23": "unexpected"})
        return dataset

    with pytest.raises(ValueError, match="feature schema"):
        load_credit_default_data(fetcher=wrong_fetcher)
