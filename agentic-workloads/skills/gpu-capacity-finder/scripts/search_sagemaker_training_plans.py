#!/usr/bin/env python3
"""
Search SageMaker Training Plan offerings across AWS regions.

Outputs JSON array of available training plans to stdout.
Requires: boto3, AWS credentials with sagemaker:SearchTrainingPlanOfferings permission.

Usage:
    python search_sagemaker_training_plans.py \
        --instance-type p5.48xlarge \
        --instance-count 4 \
        --duration-days 7 \
        --start-date 2026-07-20
"""

import argparse
import json
import sys
from datetime import datetime

import boto3
from botocore.exceptions import ClientError

REGIONS = [
    "us-east-1", "us-east-2", "us-west-1", "us-west-2",
    "eu-north-1", "eu-west-2",
    "ap-northeast-1", "ap-northeast-2", "ap-south-1",
    "ap-southeast-2", "ap-southeast-3", "ap-southeast-4",
    "sa-east-1",
]


def search_region(region: str, instance_type: str, instance_count: int,
                  duration_days: int, start_date: datetime) -> list[dict]:
    """Search a single region for SageMaker Training Plan offerings."""
    # SageMaker expects ml. prefix
    sm_instance_type = f"ml.{instance_type}" if not instance_type.startswith("ml.") else instance_type

    try:
        sm = boto3.client("sagemaker", region_name=region)
        resp = sm.search_training_plan_offerings(
            TargetResources=["training-job"],
            InstanceType=sm_instance_type,
            InstanceCount=instance_count,
            StartTimeAfter=start_date,
            DurationHours=duration_days * 24,
        )
        results = []
        for offering in resp.get("TrainingPlanOfferings", []):
            reserved = offering.get("ReservedCapacityOfferings", [])
            if reserved:
                r = reserved[0]
                results.append({
                    "source": "SageMaker Training Plan",
                    "region": region,
                    "availability_zone": r.get("AvailabilityZone", "N/A"),
                    "instance_type": r.get("InstanceType", sm_instance_type),
                    "instance_count": r.get("InstanceCount", instance_count),
                    "duration_days": duration_days,
                    "start_date": str(r.get("StartTime", "")),
                    "end_date": str(r.get("EndTime", "")),
                    "upfront_fee": offering.get("UpfrontFee", "0"),
                })
        return results
    except ClientError as e:
        code = e.response["Error"]["Code"]
        # Silently skip regions that don't support this API or instance type
        if code in ("InvalidAction", "AuthFailure", "ValidationException",
                    "UnknownOperationException"):
            return []
        return [{"region": region, "error": f"{code}: {e.response['Error']['Message']}"}]
    except Exception as e:
        if "InvalidAction" in str(e) or "UnknownOperation" in str(e):
            return []
        return [{"region": region, "error": str(e)}]


def main():
    parser = argparse.ArgumentParser(description="Search SageMaker Training Plan offerings")
    parser.add_argument("--instance-type", required=True, help="GPU instance type (without ml. prefix)")
    parser.add_argument("--instance-count", type=int, required=True, help="Number of instances")
    parser.add_argument("--duration-days", type=int, required=True, help="Duration in days")
    parser.add_argument("--start-date", default=None, help="Start date YYYY-MM-DD (default: today)")
    parser.add_argument("--regions", default=None, help="Comma-separated regions (default: all)")
    args = parser.parse_args()

    start_date = (
        datetime.strptime(args.start_date, "%Y-%m-%d") if args.start_date
        else datetime.today()
    )
    regions = args.regions.split(",") if args.regions else REGIONS

    all_results = []
    for region in regions:
        results = search_region(region, args.instance_type, args.instance_count,
                                args.duration_days, start_date)
        all_results.extend(results)

    # Separate offerings from errors
    offerings = [r for r in all_results if "error" not in r]
    errors = [r for r in all_results if "error" in r]

    output = {
        "query": {
            "instance_type": args.instance_type,
            "instance_count": args.instance_count,
            "duration_days": args.duration_days,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "regions_searched": len(regions),
        },
        "offerings": offerings,
        "errors": errors,
    }

    json.dump(output, sys.stdout, indent=2, default=str)
    print()  # trailing newline


if __name__ == "__main__":
    main()
