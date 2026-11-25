"""Run a small SageMaker Batch Transform job with the latest approved model."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3

from sagemaker_xgboost_lab.config import AWS_REGION, EXPERIMENT_NAME, S3_PREFIX
from sagemaker_xgboost_lab.data_prep import prepare_training_data

TRANSFORM_INSTANCE_TYPE = "ml.c5.xlarge"


def model_package_group_name(client: Any) -> str:
    """Use an explicit group name, or find the group created from this MLflow model name."""
    configured_name = os.getenv("MODEL_PACKAGE_GROUP_NAME")
    if configured_name:
        return configured_name

    groups = client.list_model_package_groups(
        NameContains=EXPERIMENT_NAME,
        SortBy="CreationTime",
        SortOrder="Descending",
        MaxResults=100,
    )["ModelPackageGroupSummaryList"]
    prefix = f"{EXPERIMENT_NAME}-"
    group_name = next(
        (
            str(group["ModelPackageGroupName"])
            for group in groups
            if str(group["ModelPackageGroupName"]) == EXPERIMENT_NAME
            or str(group["ModelPackageGroupName"]).startswith(prefix)
        ),
        None,
    )
    if group_name is None:
        raise RuntimeError(
            "No SageMaker Model Package Group found. Register a model in the MLflow UI."
        )
    return group_name


def latest_approved_model_package(
    client: Any,
    model_package_group_name: str,
    instance_type: str,
) -> str:
    """Return the latest approved package compatible with the instance type."""
    # Fetch only Approved packages, newest first. The loop below checks the newest package
    # first, skips versions incompatible with the current Transform instance, and
    # returns the most recent runnable version.
    packages = client.list_model_packages(
        ModelPackageGroupName=model_package_group_name,
        ModelApprovalStatus="Approved",
        SortBy="CreationTime",
        SortOrder="Descending",
        MaxResults=100,
    )["ModelPackageSummaryList"]
    if not packages:
        raise RuntimeError("No approved model version found. Approve it in SageMaker Studio.")

    for package in packages:
        package_arn = str(package["ModelPackageArn"])
        description = client.describe_model_package(ModelPackageName=package_arn)
        supported_instances = description.get("InferenceSpecification", {}).get(
            "SupportedTransformInstanceTypes",
            [],
        )
        if instance_type in supported_instances:
            return package_arn

    raise RuntimeError(f"No approved model supports {instance_type}.")


def main() -> None:
    region = os.getenv("AWS_REGION", AWS_REGION)
    bucket = os.environ["S3_BUCKET"]
    role_arn = os.environ["SAGEMAKER_EXECUTION_ROLE_ARN"]
    client = boto3.client("sagemaker", region_name=region)
    s3_client = boto3.client("s3", region_name=region)

    print("[1/4] Find the latest approved model version", flush=True)
    group_name = model_package_group_name(client)
    package_arn = latest_approved_model_package(
        client,
        group_name,
        TRANSFORM_INSTANCE_TYPE,
    )
    print(f"Approved Model Package: {package_arn}")

    suffix = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    job_name = f"xgboost-lab-batch-{suffix}"
    local_dir = Path("artifacts") / "batch" / suffix

    print("[2/4] Prepare credit default inference rows and upload them to S3")
    prepared = prepare_training_data(local_dir / "source")
    input_path = local_dir / "input.csv"
    prepared.splits.x_validation.to_csv(input_path, index=False, header=False)
    input_key = f"{S3_PREFIX}/batch/{suffix}/input/input.csv"
    output_prefix = f"{S3_PREFIX}/batch/{suffix}/output"
    s3_client.upload_file(str(input_path), bucket, input_key)

    print(f"[3/4] Run Batch Transform ({TRANSFORM_INSTANCE_TYPE})")
    # A Model Package is a version entry in the Registry. A Batch Transform Job cannot
    # reference it directly and needs a SageMaker Model, so create a temporary one for this run.
    client.create_model(
        ModelName=job_name,
        ExecutionRoleArn=role_arn,
        Containers=[{"ModelPackageName": package_arn}],
    )
    try:
        client.create_transform_job(
            TransformJobName=job_name,
            ModelName=job_name,
            # Treat each CSV line as a record and batch multiple records into one request.
            BatchStrategy="MultiRecord",
            # This is the number of concurrent mini-batch requests per instance, not in total.
            # With InstanceCount=1, up to 4 run concurrently. A single small input.csv may
            # produce only one mini-batch, so no actual parallelism may occur.
            MaxConcurrentTransforms=4,
            TransformInput={
                "DataSource": {
                    "S3DataSource": {
                        "S3DataType": "S3Prefix",
                        "S3Uri": f"s3://{bucket}/{input_key}",
                    }
                },
                "ContentType": "text/csv",
                "SplitType": "Line",
            },
            TransformOutput={
                "S3OutputPath": f"s3://{bucket}/{output_prefix}/",
                "Accept": "text/csv",
                "AssembleWith": "Line",
            },
            TransformResources={
                # Multiple input S3 files let SageMaker distribute them across instances.
                # This sample uses one instance and one input file.
                "InstanceType": TRANSFORM_INSTANCE_TYPE,
                "InstanceCount": 1,
            },
        )
        client.get_waiter("transform_job_completed_or_stopped").wait(
            TransformJobName=job_name,
            WaiterConfig={"Delay": 15, "MaxAttempts": 240},
        )

        output_path = local_dir / "predictions.csv.out"
        s3_client.download_file(
            bucket,
            f"{output_prefix}/input.csv.out",
            str(output_path),
        )
    finally:
        # Keep the Model Package; clean up only the temporary Model created for this job.
        client.delete_model(ModelName=job_name)

    print("[4/4] Download predictions")
    print(f"Batch Transform Job: {job_name}")
    print(f"Predictions: {output_path}")
    preview = output_path.read_text(encoding="utf-8").splitlines()[:5]
    print("First predicted probabilities of default next month:")
    for value in preview:
        print(f"- {value}")


if __name__ == "__main__":
    main()
