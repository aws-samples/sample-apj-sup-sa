"""Step 10: static frontend (S3, private + CloudFront OAC) and the
CloudFront distribution that fronts BOTH the static site (default
behavior) and the webui backend Lambda Function URL (/api/* behavior),
so the whole demo is served from one CloudFront URL.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.tenants import REGION
from deploy.common import account_id, client, load_state, log, save_state, tags_map, wait_until

BUCKET_NAME_PREFIX = "agentcore-mt-webui"
S3_OAC_NAME = "agentcore-mt-webui-s3-oac"
LAMBDA_OAC_NAME = "agentcore-mt-webui-lambda-oac"
ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = ROOT / "webui"


def ensure_bucket(s3, acct: str) -> str:
    bucket = f"{BUCKET_NAME_PREFIX}-{acct}"
    try:
        s3.head_bucket(Bucket=bucket)
        log(f"bucket exists: {bucket}")
    except Exception:
        s3.create_bucket(Bucket=bucket)
        log(f"created bucket: {bucket}")
        s3.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
    try:
        s3.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in tags_map().items()]})
    except Exception:  # noqa: BLE001, S110
        pass
    return bucket


def upload_site(s3, bucket: str) -> None:
    # Safety gate: a syntax error in app.js makes the ENTIRE UI silently
    # dead (no JS runs at all) with zero visible error to a user just
    # looking at the page -- this is a real failure mode that happened once
    # already. Never let a broken app.js reach the live site.
    js_path = WEBUI_DIR / "app.js"
    check = subprocess.run(["node", "--check", str(js_path)], capture_output=True, text=True)
    if check.returncode != 0:
        raise RuntimeError(
            f"ABORTING upload: {js_path} fails `node --check`:\n{check.stderr}\n"
            "Fix the syntax error before deploying -- a broken app.js means no JS runs at "
            "all, with no visible error to the user."
        )
    log(f"  node --check {js_path.name}: OK")

    content_types = {
        ".html": "text/html",
        ".js": "application/javascript",
        ".css": "text/css",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ttf": "font/ttf",
    }
    # Top-level files (index.html/app.js/styles.css) change with every
    # deploy: no-cache. assets/ (AWS design tokens, fonts, logo, icons)
    # rarely change: cache for a day.
    for f in sorted(WEBUI_DIR.rglob("*")):
        if not f.is_file():
            continue
        key = f.relative_to(WEBUI_DIR).as_posix()
        ctype = content_types.get(f.suffix)
        if ctype is None:
            raise RuntimeError(f"ABORTING upload: no content type for {key}; add one to content_types")
        cache = "public, max-age=86400" if key.startswith("assets/") else "no-cache"
        s3.put_object(Bucket=bucket, Key=key, Body=f.read_bytes(), ContentType=ctype, CacheControl=cache)
        log(f"  uploaded {key} ({ctype})")


def ensure_oac(cf, name: str, origin_type: str) -> str:
    for page in cf.get_paginator("list_origin_access_controls").paginate():
        for item in page["OriginAccessControlList"].get("Items", []):
            if item["Name"] == name:
                log(f"OAC exists: {item['Id']} ({name})")
                return item["Id"]
    resp = cf.create_origin_access_control(
        OriginAccessControlConfig={
            "Name": name,
            "SigningProtocol": "sigv4",
            "SigningBehavior": "always",
            "OriginAccessControlOriginType": origin_type,
        }
    )
    oac_id = resp["OriginAccessControl"]["Id"]
    log(f"created OAC: {oac_id} ({name}, type={origin_type})")
    return oac_id


def ensure_bucket_policy(s3, bucket: str, acct: str, distribution_arn: str) -> None:
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowCloudFrontOACRead",
                "Effect": "Allow",
                "Principal": {"Service": "cloudfront.amazonaws.com"},
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket}/*",
                "Condition": {"StringEquals": {"AWS:SourceArn": distribution_arn}},
            }
        ],
    }
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))
    log("attached bucket policy allowing only this CloudFront distribution to read")


def ensure_distribution(cf, state: dict, bucket: str, s3_oac_id: str, lambda_oac_id: str, acct: str, region: str) -> dict:
    existing_id = state.get("webui", {}).get("distribution_id")
    if existing_id:
        try:
            resp = cf.get_distribution(Id=existing_id)
            log(f"distribution exists: {existing_id} status={resp['Distribution']['Status']}")
            return resp["Distribution"]
        except Exception:
            pass

    bucket_domain = f"{bucket}.s3.{region}.amazonaws.com"
    function_url = state["webui"]["backend_function_url"]
    lambda_domain = urlparse(function_url).netloc

    config = {
        "CallerReference": f"agentcore-mt-webui-{int(time.time())}",
        "Comment": "AgentCore multi-tenant shopping assistant demo UI",
        "Enabled": True,
        "DefaultRootObject": "index.html",
        "Origins": {
            "Quantity": 2,
            "Items": [
                {
                    "Id": "s3-static-site",
                    "DomainName": bucket_domain,
                    "S3OriginConfig": {"OriginAccessIdentity": ""},
                    "OriginAccessControlId": s3_oac_id,
                },
                {
                    "Id": "lambda-backend",
                    "DomainName": lambda_domain,
                    "OriginAccessControlId": lambda_oac_id,
                    "CustomOriginConfig": {
                        "HTTPPort": 80,
                        "HTTPSPort": 443,
                        "OriginProtocolPolicy": "https-only",
                        "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
                        "OriginReadTimeout": 30,
                        "OriginKeepaliveTimeout": 5,
                    },
                },
            ],
        },
        "DefaultCacheBehavior": {
            "TargetOriginId": "s3-static-site",
            "ViewerProtocolPolicy": "redirect-to-https",
            "AllowedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
            "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",  # AWS managed: CachingOptimized
            "Compress": True,
        },
        "CacheBehaviors": {
            "Quantity": 1,
            "Items": [
                {
                    "PathPattern": "/api/*",
                    "TargetOriginId": "lambda-backend",
                    "ViewerProtocolPolicy": "https-only",
                    "AllowedMethods": {
                        "Quantity": 7,
                        "Items": ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"],
                        "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
                    },
                    # AWS managed: CachingDisabled
                    "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",
                    # AWS managed: AllViewerExceptHostHeader (forwards headers/cookies/querystrings, strips Host)
                    "OriginRequestPolicyId": "b689b0a8-53d0-40ab-baf2-68738e2966ac",
                    "Compress": True,
                }
            ],
        },
        "PriceClass": "PriceClass_100",
    }

    resp = cf.create_distribution(DistributionConfig=config)
    dist = resp["Distribution"]
    log(f"created distribution: {dist['Id']}")
    return dist


def main() -> None:
    state = load_state()
    acct = account_id()
    region = REGION
    s3 = client("s3")
    cf = client("cloudfront")
    lam = client("lambda")

    bucket = ensure_bucket(s3, acct)
    upload_site(s3, bucket)
    s3_oac_id = ensure_oac(cf, S3_OAC_NAME, "s3")
    lambda_oac_id = ensure_oac(cf, LAMBDA_OAC_NAME, "lambda")

    dist = ensure_distribution(cf, state, bucket, s3_oac_id, lambda_oac_id, acct, region)
    dist_id = dist["Id"]
    dist_arn = dist.get("ARN") or f"arn:aws:cloudfront::{acct}:distribution/{dist_id}"
    domain_name = dist["DomainName"]

    ensure_bucket_policy(s3, bucket, acct, dist_arn)

    # CloudFront's Lambda OAC signs every request to the Function URL with
    # SigV4 as the cloudfront.amazonaws.com service principal. This
    # resource-based Lambda permission, scoped to this one distribution's
    # ARN, is what authorizes those signed calls (the Function URL itself
    # is AuthType=AWS_IAM, i.e. never public -- see deploy/09_webui_backend.py).
    try:
        lam.add_permission(
            FunctionName="agentcore-mt-webui-backend",
            StatementId="allow-this-cloudfront-distribution-oac",
            Action="lambda:InvokeFunctionUrl",
            Principal="cloudfront.amazonaws.com",
            SourceArn=dist_arn,
            FunctionUrlAuthType="AWS_IAM",
        )
        log("scoped Lambda Function URL invoke permission to this CloudFront distribution (via OAC)")
    except lam.exceptions.ResourceConflictException:
        pass
    # CloudFront's Lambda OAC documentation requires a SECOND permission
    # statement (lambda:InvokeFunction, no FunctionUrlAuthType condition) in
    # addition to lambda:InvokeFunctionUrl -- without this second statement
    # every request is rejected with a bare "403 Forbidden" from the
    # Function URL service, even though the InvokeFunctionUrl permission
    # and OAC config both look correct. Confirmed empirically against the
    # official AWS doc example after debugging the 403.
    try:
        lam.add_permission(
            FunctionName="agentcore-mt-webui-backend",
            StatementId="allow-this-cloudfront-distribution-invoke",
            Action="lambda:InvokeFunction",
            Principal="cloudfront.amazonaws.com",
            SourceArn=dist_arn,
        )
        log("added required second permission (lambda:InvokeFunction) for CloudFront OAC")
    except lam.exceptions.ResourceConflictException:
        pass

    state["webui"]["bucket"] = bucket
    state["webui"]["distribution_id"] = dist_id
    state["webui"]["distribution_domain"] = domain_name
    state["webui"]["cloudfront_url"] = f"https://{domain_name}"
    save_state(state)

    log("Web UI CDN setup kicked off.")
    log(f"  bucket={bucket}")
    log(f"  distribution_id={dist_id}")
    log(f"  cloudfront_url=https://{domain_name}")
    log("  NOTE: CloudFront distributions take ~10-20 minutes to reach Status=Deployed on first create.")


if __name__ == "__main__":
    main()
