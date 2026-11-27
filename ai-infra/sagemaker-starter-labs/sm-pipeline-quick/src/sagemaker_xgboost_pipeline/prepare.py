"""Validate a UCI CSV snapshot and write stratified train/validation/test CSVs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

RANDOM_SEED = 42


def prepare_data(input_dir: Path, output_dir: Path) -> dict:
    source = json.loads((input_dir / "source.json").read_text(encoding="utf-8"))
    csv_path = input_dir / "data.csv"
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    if source["dataset_id"] != 350 or digest != source["sha256"]:
        raise ValueError("Expected a verified UCI dataset 350 input snapshot.")
    feature_names = source["feature_names"]
    if len(feature_names) != 23 or len(set(feature_names)) != 23 or "label" in feature_names:
        raise ValueError("The input must describe 23 distinct UCI features.")
    frame = pd.read_csv(csv_path)
    if frame.columns.tolist() != ["label", *feature_names]:
        raise ValueError("CSV columns must match the source metadata in label-first order.")
    frame = frame.apply(pd.to_numeric, errors="raise")
    if not np.isfinite(frame.to_numpy()).all():
        raise ValueError("Input features and labels must be finite numeric values.")
    if set(frame["label"].unique()) != {0, 1}:
        raise ValueError("The input must contain binary labels 0 and 1.")

    train, remaining = train_test_split(
        frame, test_size=0.30, random_state=RANDOM_SEED, stratify=frame["label"]
    )
    validation, test = train_test_split(
        remaining, test_size=0.50, random_state=RANDOM_SEED, stratify=remaining["label"]
    )
    splits = {"train": train, "validation": validation, "test": test}
    for name, split in splits.items():
        if set(split["label"].unique()) != {0, 1}:
            raise ValueError(f"The {name} split must contain labels 0 and 1.")
        destination = output_dir / name
        destination.mkdir(parents=True, exist_ok=True)
        split.to_csv(destination / f"{name}.csv", index=False, header=False)

    summary = {
        "dataset_id": source["dataset_id"],
        "dataset_name": source["dataset_name"],
        "source_sha256": digest,
        "label_column": "label",
        "feature_names": feature_names,
        "random_seed": RANDOM_SEED,
        "split_rows": {name: len(split) for name, split in splits.items()},
    }
    metadata_dir = output_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (metadata_dir / "data-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("/opt/ml/processing/input/data"))
    parser.add_argument("--output-dir", type=Path, default=Path("/opt/ml/processing/output"))
    args = parser.parse_args()
    summary = prepare_data(args.input_dir, args.output_dir)
    print(json.dumps(summary["split_rows"]))


if __name__ == "__main__":
    main()
