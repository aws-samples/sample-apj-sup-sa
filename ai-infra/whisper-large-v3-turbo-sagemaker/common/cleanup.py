"""
Delete a Whisper SageMaker endpoint, its config, the model, and any autoscaling
registration to stop billing and leave no orphaned resources.

GPU endpoints bill per hour while running, so tear them down when not in use.

Usage:
    python common/cleanup.py --endpoint-name whisper-vllm --region ap-south-1
"""

import argparse

import boto3
from botocore.exceptions import ClientError


def parse_args():
    p = argparse.ArgumentParser(description="Delete a SageMaker endpoint and related resources.")
    p.add_argument("--endpoint-name", required=True)
    p.add_argument("--region", default=None)
    p.add_argument("--variant-name", default="AllTraffic",
                   help="Production variant name (for autoscaling deregistration).")
    return p.parse_args()


def _safe(label, fn):
    try:
        fn()
        print(f"Deleted {label}.")
    except ClientError as exc:
        print(f"Skipped {label}: {exc.response['Error']['Message']}")


def main():
    args = parse_args()
    sm = boto3.client("sagemaker", region_name=args.region)
    aas = boto3.client("application-autoscaling", region_name=args.region)
    name = args.endpoint_name

    # Deregister autoscaling first (if any).
    resource_id = f"endpoint/{name}/variant/{args.variant_name}"
    _safe(f"autoscaling target '{resource_id}'", lambda: aas.deregister_scalable_target(
        ServiceNamespace="sagemaker",
        ResourceId=resource_id,
        ScalableDimension="sagemaker:variant:DesiredInstanceCount",
    ))

    # Look up the model name behind the endpoint config before deleting it.
    model_name = None
    try:
        cfg = sm.describe_endpoint_config(EndpointConfigName=name)
        model_name = cfg["ProductionVariants"][0]["ModelName"]
    except ClientError:
        pass

    _safe(f"endpoint '{name}'", lambda: sm.delete_endpoint(EndpointName=name))
    _safe(f"endpoint config '{name}'", lambda: sm.delete_endpoint_config(EndpointConfigName=name))
    if model_name:
        _safe(f"model '{model_name}'", lambda: sm.delete_model(ModelName=model_name))


if __name__ == "__main__":
    main()
