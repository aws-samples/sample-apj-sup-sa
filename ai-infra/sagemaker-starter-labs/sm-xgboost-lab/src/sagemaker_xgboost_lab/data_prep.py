"""Download a public dataset and create SageMaker XGBoost CSV inputs."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from ucimlrepo import fetch_ucirepo

from sagemaker_xgboost_lab.config import (
    DATASET_ID,
    DATASET_NAME,
    RANDOM_SEED,
    VALIDATION_FRACTION,
)

SOURCE_FEATURE_NAMES = tuple(f"X{index}" for index in range(1, 24))
FEATURE_NAMES = (
    "LIMIT_BAL",
    "SEX",
    "EDUCATION",
    "MARRIAGE",
    "AGE",
    "PAY_0",
    "PAY_2",
    "PAY_3",
    "PAY_4",
    "PAY_5",
    "PAY_6",
    "BILL_AMT1",
    "BILL_AMT2",
    "BILL_AMT3",
    "BILL_AMT4",
    "BILL_AMT5",
    "BILL_AMT6",
    "PAY_AMT1",
    "PAY_AMT2",
    "PAY_AMT3",
    "PAY_AMT4",
    "PAY_AMT5",
    "PAY_AMT6",
)


@dataclass(frozen=True)
class DataSplits:
    x_train: pd.DataFrame
    y_train: pd.Series
    x_validation: pd.DataFrame
    y_validation: pd.Series


@dataclass(frozen=True)
class PreparedData:
    splits: DataSplits
    train_csv: Path
    validation_csv: Path
    summary_json: Path


def load_credit_default_data(
    fetcher: Callable[..., Any] = fetch_ucirepo,
) -> tuple[pd.DataFrame, pd.Series]:
    """Load and name the numeric UCI credit card default features."""

    dataset = fetcher(id=DATASET_ID)
    features = dataset.data.features.copy()
    targets = dataset.data.targets
    if not isinstance(features, pd.DataFrame) or not isinstance(targets, pd.DataFrame):
        raise ValueError("The UCI dataset did not return tabular features and targets.")
    if targets.shape[1] != 1:
        raise ValueError("This example expects exactly one target column.")
    if tuple(features.columns) != SOURCE_FEATURE_NAMES:
        raise ValueError("The UCI feature schema does not match dataset 350.")

    # Rename the UCI API's X1-X23 columns to the readable names from the original paper.
    features.columns = FEATURE_NAMES
    numeric_features = features.apply(pd.to_numeric, errors="raise").astype(np.float32)
    labels = pd.to_numeric(targets.iloc[:, 0], errors="raise").astype(np.int32)
    if numeric_features.isna().any().any() or labels.isna().any():
        raise ValueError("The dataset contains missing values.")
    if not np.isfinite(numeric_features.to_numpy()).all():
        raise ValueError("The dataset contains non-finite feature values.")
    if set(labels.unique()) != {0, 1}:
        raise ValueError("This example requires binary labels 0 and 1.")
    labels.name = "label"
    return numeric_features, labels


def stratified_split(
    features: pd.DataFrame,
    labels: pd.Series,
    validation_fraction: float = VALIDATION_FRACTION,
    random_seed: int = RANDOM_SEED,
) -> DataSplits:
    """Create a reproducible train/validation split with both classes in each set."""

    if len(features) != len(labels) or len(features) == 0:
        raise ValueError("Features and labels must have the same non-zero row count.")
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be between 0 and 1.")

    # A fixed seed produces the same train/validation split on every run.
    generator = np.random.default_rng(random_seed)
    validation_indices: list[int] = []
    train_indices: list[int] = []
    label_values = labels.to_numpy()
    for label in (0, 1):
        # Sample each class separately so both splits keep a similar class ratio.
        # Even a small validation set needs both 0s and 1s for a stable ROC-AUC.
        class_indices = np.flatnonzero(label_values == label)
        if len(class_indices) < 2:
            raise ValueError("Each class needs at least two rows for a stratified split.")
        generator.shuffle(class_indices)
        validation_count = max(1, round(len(class_indices) * validation_fraction))
        validation_count = min(validation_count, len(class_indices) - 1)
        validation_indices.extend(class_indices[:validation_count])
        train_indices.extend(class_indices[validation_count:])

    generator.shuffle(train_indices)
    generator.shuffle(validation_indices)
    return DataSplits(
        x_train=features.iloc[train_indices].reset_index(drop=True),
        y_train=labels.iloc[train_indices].reset_index(drop=True),
        x_validation=features.iloc[validation_indices].reset_index(drop=True),
        y_validation=labels.iloc[validation_indices].reset_index(drop=True),
    )


def prepare_training_data(
    destination: Path,
    fetcher: Callable[..., Any] = fetch_ucirepo,
) -> PreparedData:
    """Write label-first, headerless CSV files used by built-in XGBoost."""

    features, labels = load_credit_default_data(fetcher)
    splits = stratified_split(features, labels)
    destination.mkdir(parents=True, exist_ok=True)
    train_csv = destination / "train.csv"
    validation_csv = destination / "validation.csv"
    summary_json = destination / "data-summary.json"

    # Built-in XGBoost reads the first column as the label, so write headerless label-first CSV.
    _write_label_first(splits.x_train, splits.y_train, train_csv)
    _write_label_first(splits.x_validation, splits.y_validation, validation_csv)
    # Save feature order and basic split info for the training/autolog code to check.
    summary_json.write_text(
        json.dumps(
            {
                "dataset": DATASET_NAME,
                "uci_dataset_id": DATASET_ID,
                "target": {
                    "name": "default_payment_next_month",
                    "positive_label": 1,
                    "positive_label_meaning": "default payment next month",
                },
                "feature_names": features.columns.tolist(),
                "rows": {
                    "train": len(splits.x_train),
                    "validation": len(splits.x_validation),
                },
                "class_distribution": {
                    "train": _class_counts(splits.y_train),
                    "validation": _class_counts(splits.y_validation),
                },
                "random_seed": RANDOM_SEED,
                "validation_fraction": VALIDATION_FRACTION,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return PreparedData(
        splits=splits,
        train_csv=train_csv,
        validation_csv=validation_csv,
        summary_json=summary_json,
    )


def _class_counts(labels: pd.Series) -> dict[str, int]:
    counts = labels.value_counts()
    return {"non_default": int(counts.get(0, 0)), "default": int(counts.get(1, 0))}


def _write_label_first(features: pd.DataFrame, labels: pd.Series, path: Path) -> None:
    # Inference input has no label, but the first column of training input must be the label.
    pd.concat([labels.rename("label"), features], axis=1).to_csv(
        path,
        index=False,
        header=False,
    )
