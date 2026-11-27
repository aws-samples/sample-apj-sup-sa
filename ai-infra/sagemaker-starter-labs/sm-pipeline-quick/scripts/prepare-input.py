"""Reuse the XGBoost lab's public UCI loader and optionally upload a versioned snapshot.

Run with the sibling lab's uv environment, as done by scripts/setup.sh.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import boto3
import pandas as pd
from sagemaker_xgboost_lab.config import DATASET_ID, DATASET_NAME
from sagemaker_xgboost_lab.data_prep import load_credit_default_data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/input"))
    parser.add_argument("--bucket")
    parser.add_argument("--region", default="ap-northeast-2")
    args = parser.parse_args()

    features, labels = load_credit_default_data()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    data_path = args.output_dir / "data.csv"
    pd.concat([labels.rename("label"), features], axis=1).to_csv(data_path, index=False)
    digest = hashlib.sha256(data_path.read_bytes()).hexdigest()
    source = {
        "dataset_id": DATASET_ID,
        "dataset_name": DATASET_NAME,
        "source_url": "https://archive.ics.uci.edu/dataset/350/default+of+credit+card+clients",
        "doi": "10.24432/C55S3H",
        "license": "CC BY 4.0",
        "feature_names": features.columns.tolist(),
        "rows": len(features),
        "sha256": digest,
    }
    source_path = args.output_dir / "source.json"
    source_path.write_text(json.dumps(source, indent=2), encoding="utf-8")
    print(
        f"Prepared {len(features)} rows and {len(features.columns)} features in {args.output_dir}"
    )

    if args.bucket:
        prefix = f"sagemaker-xgboost-lab/credit-card-default/pipeline/inputs/uci-350/{digest}"
        client = boto3.client("s3", region_name=args.region)
        client.upload_file(str(data_path), args.bucket, f"{prefix}/data.csv")
        client.upload_file(str(source_path), args.bucket, f"{prefix}/source.json")
        uri = f"s3://{args.bucket}/{prefix}/"
        location = args.output_dir / "input-location.json"
        temporary = location.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"input_data_s3_uri": uri, "region": args.region}, indent=2),
            encoding="utf-8",
        )
        temporary.replace(location)
        print(f"InputDataS3Uri={uri}")


if __name__ == "__main__":
    main()
