#!/usr/bin/env python3
"""
Search EC2 Capacity Block offerings across AWS regions.

Outputs JSON array of available capacity blocks to stdout.
Requires: boto3, AWS credentials with ec2:DescribeCapacityBlockOfferings permission.

Usage:
    python search_ec2_capacity_blocks.py \
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
    """Search a single region for capacity block offerings."""
    try:
        ec2 = boto3.client("ec2", region_name=region)
        resp = ec2.describe_capacity_block_offerings(
            InstanceType=instance_type,
            InstanceCount=instance_count,
            CapacityDurationHours=duration_days * 24,
            StartDateRange=start_date,
            MaxResults=20,
        )
        results = []
        for o in resp.get("CapacityBlockOfferings", []):
            results.append({
                "source": "EC2 Capacity Block",
                "region": region,
                "availability_zone": o.get("AvailabilityZone", "N/A"),
                "instance_type": instance_type,
                "instance_count": o.get("InstanceCount", instance_count),
                "duration_days": duration_days,
                "start_date": str(o.get("StartDate", "")),
                "end_date": str(o.get("EndDate", "")),
                "upfront_fee": o.get("UpfrontFee", "0"),
            })
        return results
    except ClientError as e:
        code = e.response["Error"]["Code"]
        # Silently skip regions that don't support this instance type
        if code in ("InvalidParameterValue", "Unsupported", "OptInRequired"):
            return []
        return [{"region": region, "error": f"{code}: {e.response['Error']['Message']}"}]
    except Exception as e:
        return [{"region": region, "error": str(e)}]


def main():
    parser = argparse.ArgumentParser(description="Search EC2 Capacity Block offerings")
    parser.add_argument("--instance-type", required=True, help="GPU instance type")
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
