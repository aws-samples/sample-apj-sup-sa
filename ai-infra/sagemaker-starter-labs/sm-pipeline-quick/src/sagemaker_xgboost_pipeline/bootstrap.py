"""Create the one-time SageMaker Model Package Group used by the Pipeline."""

from __future__ import annotations

import argparse
import os

import boto3

from sagemaker_xgboost_pipeline.config import AWS_REGION, MODEL_PACKAGE_GROUP_NAME


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model-package-group-name",
        default=os.getenv("MODEL_PACKAGE_GROUP_NAME", MODEL_PACKAGE_GROUP_NAME),
    )
    parser.add_argument("--region", default=os.getenv("AWS_REGION", AWS_REGION))
    args = parser.parse_args()

    client = boto3.client("sagemaker", region_name=args.region)
    pages = client.get_paginator("list_model_package_groups").paginate(
        NameContains=args.model_package_group_name
    )
    for page in pages:
        if any(
            group["ModelPackageGroupName"] == args.model_package_group_name
            for group in page["ModelPackageGroupSummaryList"]
        ):
            print(f"Model Package Group already exists: {args.model_package_group_name}")
            return

    client.create_model_package_group(
        ModelPackageGroupName=args.model_package_group_name,
        ModelPackageGroupDescription="UCI XGBoost models that pass the Pipeline AUC gate",
    )
    print(f"Created Model Package Group: {args.model_package_group_name}")


if __name__ == "__main__":
    main()
