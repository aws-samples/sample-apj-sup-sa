#!/usr/bin/env python3
"""
Deploy Cube YAML data models to the Cube EC2 instance via S3 and SSM.

Uploads model YAML files from dataset/cube_models/{model_set}_model/ to S3,
then uses SSM Run Command to sync them to /cube/conf/model/ on the EC2 instance.
Finally verifies that Cube Core has loaded the expected number of cubes.

Usage:
    python3 deploy_cube_models.py --model-set initial
    python3 deploy_cube_models.py --model-set final
"""

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

import boto3

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
REPO_ROOT = ROOT_DIR.parent.parent  # workspace root: app/agentcore_strands/infra → app/agentcore_strands → app → repo root

REGION = "us-east-1"
VALID_MODEL_SETS = {"initial", "final"}
EXPECTED_CUBE_COUNTS = {"initial": 5, "final": 5}


def get_cloudformation_outputs():
    """Resolve CubeConfigBucketName, CubeInstanceId, and CubePrivateIp
    from CloudFormation main-stack outputs."""
    cfn = boto3.client("cloudformation", region_name=REGION)

    # Try main-stack outputs first
    try:
        resp = cfn.describe_stacks(StackName="main-stack")
        outputs = {
            o["OutputKey"]: o["OutputValue"]
            for o in resp["Stacks"][0].get("Outputs", [])
        }
        required = ["CubeConfigBucketName", "CubeInstanceId", "CubePrivateIp"]
        if all(k in outputs for k in required):
            return outputs
    except Exception as e:
        print(f"Note: Could not read main-stack outputs: {e}")

    # Fallback: find the nested CubeStack and read its outputs directly
    try:
        resources = cfn.list_stack_resources(StackName="main-stack")
        for r in resources["StackResourceSummaries"]:
            if "Cube" in r.get("LogicalResourceId", ""):
                nested_stack_id = r["PhysicalResourceId"]
                nested = cfn.describe_stacks(StackName=nested_stack_id)
                nested_outputs = {
                    o["OutputKey"]: o["OutputValue"]
                    for o in nested["Stacks"][0].get("Outputs", [])
                }
                required = ["CubeConfigBucketName", "CubeInstanceId", "CubePrivateIp"]
                if all(k in nested_outputs for k in required):
                    return nested_outputs
    except Exception as e:
        print(f"Note: Could not read nested CubeStack outputs: {e}")

    raise Exception(
        "Could not resolve Cube infrastructure values from CloudFormation. "
        "Ensure the CubeStack has been deployed (check main-stack status)."
    )


def upload_models_to_s3(model_set, bucket_name):
    """Upload YAML files from dataset/cube_models/{model_set}_model/ to
    s3://{bucket_name}/models/{model_set}/."""
    model_dir = REPO_ROOT / "dataset" / "cube_models" / f"{model_set}_model"
    if not model_dir.exists():
        raise FileNotFoundError(f"Model directory not found: {model_dir}")

    yaml_files = list(model_dir.glob("*.yml"))
    if not yaml_files:
        raise FileNotFoundError(f"No YAML files found in {model_dir}")

    s3 = boto3.client("s3", region_name=REGION)
    print(f"\nUploading {len(yaml_files)} model files to s3://{bucket_name}/models/{model_set}/")

    for yaml_file in yaml_files:
        s3_key = f"models/{model_set}/{yaml_file.name}"
        try:
            s3.upload_file(str(yaml_file), bucket_name, s3_key)
            print(f"  [OK] {yaml_file.name} → s3://{bucket_name}/{s3_key}")
        except Exception as e:
            raise Exception(f"S3 upload failed for {yaml_file.name}: {e}")

    print(f"[OK] All {len(yaml_files)} files uploaded to S3")


def deploy_models_via_ssm(instance_id, bucket_name, model_set):
    """Execute SSM Run Command to clear /cube/conf/model/ and sync from S3.
    Repairs the AWS CLI if needed (missing dateutil module).
    Returns SSM command result with status and output."""
    ssm = boto3.client("ssm", region_name=REGION)

    commands = [
        # Fix broken AWS CLI: reinstall missing deps into system site-packages
        # (the aws shebang uses python3 -s which skips user site-packages)
        "pip3 install --target /usr/lib/python3.9/site-packages python-dateutil prompt_toolkit wcwidth 2>/dev/null || true",
        "rm -f /cube/conf/model/*.yml",
        f"aws s3 sync s3://{bucket_name}/models/{model_set}/ /cube/conf/model/",
        "ls -la /cube/conf/model/",
    ]

    print(f"\nDeploying {model_set} models via SSM Run Command...")
    print(f"  Instance: {instance_id}")
    print(f"  Source: s3://{bucket_name}/models/{model_set}/")
    print(f"  Target: /cube/conf/model/")

    try:
        response = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": commands},
            TimeoutSeconds=120,
            Comment=f"Deploy Cube {model_set} models from S3",
        )
    except Exception as e:
        raise Exception(f"SSM send_command failed: {e}")

    command_id = response["Command"]["CommandId"]
    print(f"  Command ID: {command_id}")

    # Wait for the command to complete
    print("  Waiting for SSM command to complete...")
    for _ in range(30):
        time.sleep(5)
        try:
            result = ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id,
            )
            status = result["Status"]
            if status in ("Success", "Failed", "TimedOut", "Cancelled"):
                break
        except ssm.exceptions.InvocationDoesNotExist:
            continue
    else:
        raise Exception(f"SSM command {command_id} did not complete within timeout")

    output = result.get("StandardOutputContent", "")
    error_output = result.get("StandardErrorContent", "")

    if status != "Success":
        msg = (
            f"SSM command failed with status: {status}\n"
            f"  Output: {output}\n"
            f"  Error: {error_output}"
        )
        raise Exception(msg)

    print(f"  [OK] SSM command completed successfully")
    if output:
        print(f"  Output:\n{output}")

    return {"status": status, "output": output, "error": error_output}


def verify_cube_models(cube_endpoint, expected_cube_count):
    """Call GET /cubejs-api/v1/meta and verify the expected number of cubes loaded.
    Returns True if verification passes, False otherwise."""
    # Support both full URL (http://host:4000) and bare IP/hostname
    if cube_endpoint.startswith("http"):
        base = cube_endpoint.rstrip("/")
    else:
        base = f"http://{cube_endpoint}:4000"
    url = f"{base}/cubejs-api/v1/meta"
    print(f"\nVerifying Cube models at {url}...")
    print(f"  Expected cube count: {expected_cube_count}")

    # Allow time for Cube dev mode to auto-reload models
    print("  Waiting 10s for Cube to reload models...")
    time.sleep(10)

    max_retries = 3
    actual_count = 0
    for attempt in range(1, max_retries + 1):
        try:
            # Cube runs on a private EC2 host over http; reject any non-http(s)
            # scheme so a malformed endpoint can't read file:// (B310 mitigation).
            if not url.lower().startswith(("http://", "https://")):
                raise ValueError(f"unexpected Cube URL scheme: {url[:40]}")
            req = Request(url)
            with urlopen(req, timeout=30) as resp:  # nosec B310 - scheme constrained to http/https above
                data = json.loads(resp.read().decode())

            cubes = data.get("cubes", [])
            actual_count = len(cubes)
            cube_names = [c.get("name", "unknown") for c in cubes]

            if actual_count == expected_cube_count:
                print(f"  [OK] Verification passed: {actual_count} cubes loaded")
                print(f"  Cubes: {cube_names}")
                return True
            else:
                print(
                    f"  Attempt {attempt}/{max_retries}: "
                    f"Expected {expected_cube_count} cubes, got {actual_count}"
                )
                print(f"  Cubes found: {cube_names}")
                if attempt < max_retries:
                    print("  Retrying in 10s...")
                    time.sleep(10)
        except (URLError, Exception) as e:
            print(f"  Attempt {attempt}/{max_retries}: Failed to reach Cube API: {e}")
            if attempt < max_retries:
                print("  Retrying in 10s...")
                time.sleep(10)

    print(
        f"  ❌ Verification failed: expected {expected_cube_count} cubes, "
        f"got {actual_count}"
    )
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Deploy Cube YAML data models via S3 and SSM"
    )
    parser.add_argument(
        "--model-set",
        required=True,
        choices=sorted(VALID_MODEL_SETS),
        help="Model set to deploy: 'initial' or 'final'",
    )
    args = parser.parse_args()
    model_set = args.model_set

    print(f"Deploying Cube {model_set} models")
    print("=" * 60)

    # Resolve infrastructure values from CloudFormation
    print("\nResolving infrastructure from CloudFormation main-stack...")
    outputs = get_cloudformation_outputs()
    bucket_name = outputs["CubeConfigBucketName"]
    instance_id = outputs["CubeInstanceId"]
    cube_private_ip = outputs["CubePrivateIp"]
    cube_endpoint = outputs.get("CubeEndpoint", cube_private_ip)
    print(f"  Bucket: {bucket_name}")
    print(f"  Instance: {instance_id}")
    print(f"  Cube IP: {cube_private_ip}")
    print(f"  Cube Endpoint: {cube_endpoint}")

    # Step 1: Upload models to S3
    try:
        upload_models_to_s3(model_set, bucket_name)
    except Exception as e:
        print(f"\n❌ S3 upload failed: {e}")
        print("Stopping before SSM command.")
        sys.exit(1)

    # Step 2: Deploy models via SSM
    try:
        deploy_models_via_ssm(instance_id, bucket_name, model_set)
    except Exception as e:
        print(f"\n❌ SSM deployment failed: {e}")
        sys.exit(1)

    # Step 3: Verify models loaded — use private IP (works from within VPC)
    expected_count = EXPECTED_CUBE_COUNTS[model_set]
    if not verify_cube_models(cube_private_ip, expected_count):
        print(f"\n❌ Model verification failed")
        sys.exit(1)

    print("\n" + "=" * 60)
    print(f"[OK] Cube {model_set} models deployed successfully!")


if __name__ == "__main__":
    main()
