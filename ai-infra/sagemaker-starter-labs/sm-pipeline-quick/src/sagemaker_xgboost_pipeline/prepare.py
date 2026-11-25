"""Read Redshift Parquet extracts, make simple pandas features, and write time splits."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import awswrangler as wr

from sagemaker_xgboost_pipeline.config import LABEL_COLUMN
from sagemaker_xgboost_pipeline.features import (
    build_feature_frame,
    fit_transformer,
    model_feature_columns,
    model_frame,
    split_by_time,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-prefix", required=True)
    parser.add_argument("--validation-start", required=True)
    parser.add_argument("--oot-start", required=True)
    parser.add_argument("--label-column", default=LABEL_COLUMN)
    parser.add_argument("--output-dir", default="/opt/ml/processing/output")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw_prefix = args.raw_prefix.rstrip("/")
    applications = wr.s3.read_parquet(f"{raw_prefix}/applications/")
    seller_profiles = wr.s3.read_parquet(f"{raw_prefix}/seller_profiles/")
    settlements = wr.s3.read_parquet(f"{raw_prefix}/settlements/")

    # `features` is the joined pandas DataFrame. The following steps select its model inputs,
    # split it over time, and turn those inputs into the numeric matrix XGBoost consumes.
    features = build_feature_frame(applications, seller_profiles, settlements)
    train, validation, oot = split_by_time(
        features,
        args.validation_start,
        args.oot_start,
        args.label_column,
    )
    feature_columns = model_feature_columns(features, args.label_column)
    transformer = fit_transformer(train, feature_columns)

    output_dir = Path(args.output_dir)
    for name, frame in {"train": train, "validation": validation, "oot": oot}.items():
        path = output_dir / name
        path.mkdir(parents=True, exist_ok=True)
        model_frame(frame, args.label_column, feature_columns, transformer).to_csv(
            path / f"{name}.csv", index=False, header=False
        )

    metadata_dir = output_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "label_column": args.label_column,
        "feature_names": feature_columns,
        "feature_transform": transformer.as_dict(),
        "raw_prefix": raw_prefix,
        "split_rows": {"train": len(train), "validation": len(validation), "oot": len(oot)},
        "date_ranges": {
            "train": [
                str(train.observation_date.min().date()),
                str(train.observation_date.max().date()),
            ],
            "validation": [
                str(validation.observation_date.min().date()),
                str(validation.observation_date.max().date()),
            ],
            "oot": [str(oot.observation_date.min().date()), str(oot.observation_date.max().date())],
        },
    }
    (metadata_dir / "data-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
