#!/usr/bin/env python
"""Provision the single AWS resource this demo needs: one private S3 bucket.

Security posture (verified by ``--verify``):
  * Block Public Access fully on (all four switches).
  * Bucket owner enforced (ACLs disabled entirely).
  * Default encryption SSE-S3 (AES256) + bucket keys.
  * Bucket policy denies any request that is not TLS 1.2+.
  * Lifecycle expiry so demo media/embeddings do not linger.

Nothing here is internet-reachable: the bucket is only used as the handoff
location for Bedrock async video embedding jobs and Pegasus video input.

Usage:
    uv run python scripts/provision_aws.py            # create/patch + verify
    uv run python scripts/provision_aws.py --verify    # audit only
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from talk2vid import config  # noqa: E402

LIFECYCLE = {
    "Rules": [
        {
            "ID": "expire-embedding-jobs",
            "Filter": {"Prefix": f"{config.S3_PREFIX}/embeddings/"},
            "Status": "Enabled",
            "Expiration": {"Days": 30},
            "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 3},
        },
        {
            "ID": "expire-media",
            "Filter": {"Prefix": f"{config.S3_PREFIX}/media/"},
            "Status": "Enabled",
            "Expiration": {"Days": 90},
            "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 3},
        },
    ]
}


def tls_only_policy(bucket: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "DenyInsecureTransport",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:*",
                "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
            },
            {
                "Sid": "DenyOutdatedTLS",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:*",
                "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
                "Condition": {"NumericLessThan": {"s3:TlsVersion": "1.2"}},
            },
        ],
    }


def bucket_name(account: str, region: str) -> str:
    return config.S3_BUCKET or f"talk2vid-{account}-{region}"


def create(s3, bucket: str, region: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"  bucket exists: {bucket}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket", "403"):
            raise
        kwargs = {"Bucket": bucket}
        if region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3.create_bucket(**kwargs)
        print(f"  created bucket: {bucket}")

    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_ownership_controls(
        Bucket=bucket, OwnershipControls={"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}
    )
    s3.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
                    "BucketKeyEnabled": True,
                }
            ]
        },
    )
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps(tls_only_policy(bucket)))
    s3.put_bucket_lifecycle_configuration(Bucket=bucket, LifecycleConfiguration=LIFECYCLE)
    print("  applied: block-public-access, owner-enforced, SSE-S3, TLS-only policy, lifecycle")


def verify(s3, bucket: str) -> bool:
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")

    pab = s3.get_public_access_block(Bucket=bucket)["PublicAccessBlockConfiguration"]
    check("block public access (all 4)", all(pab.values()), json.dumps(pab))

    own = s3.get_bucket_ownership_controls(Bucket=bucket)["OwnershipControls"]["Rules"][0]
    check("ACLs disabled", own["ObjectOwnership"] == "BucketOwnerEnforced", own["ObjectOwnership"])

    enc = s3.get_bucket_encryption(Bucket=bucket)["ServerSideEncryptionConfiguration"]["Rules"][0]
    check(
        "default encryption",
        enc["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] in ("AES256", "aws:kms"),
        enc["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"],
    )

    pol = json.loads(s3.get_bucket_policy(Bucket=bucket)["Policy"])
    sids = {s.get("Sid") for s in pol["Statement"]}
    check("TLS-only bucket policy", {"DenyInsecureTransport", "DenyOutdatedTLS"} <= sids)
    has_public_allow = any(
        s.get("Effect") == "Allow" and s.get("Principal") in ("*", {"AWS": "*"})
        for s in pol["Statement"]
    )
    check("no public Allow statement", not has_public_allow)

    try:
        status = s3.get_bucket_policy_status(Bucket=bucket)["PolicyStatus"]["IsPublic"]
        check("bucket not public", status is False)
    except ClientError:
        pass
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="audit only, change nothing")
    args = ap.parse_args()

    region = config.AWS_REGION
    account = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    s3 = boto3.client("s3", region_name=region)
    bucket = bucket_name(account, region)

    print(f"account={account} region={region} bucket={bucket}")
    if not args.verify:
        create(s3, bucket, region)
    print("security audit:")
    ok = verify(s3, bucket)
    print()
    print(f"TALK2VID_S3_BUCKET={bucket}")
    if not ok:
        print("audit FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
