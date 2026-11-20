"""Step 13: abuse controls for the public web UI (no sign-in).

The site calls Bedrock on every chat turn, so anyone with the URL can spend
money. Three bounds, outermost first:

  1. WAF web ACL (scope CLOUDFRONT, us-east-1) attached to the distribution:
     - a rate-based rule: at most RATE_LIMIT requests per IP per 5 minutes
       on /api/* (the static site is not counted), answered with 429;
     - the AWS managed common rule set (OWASP-style body/header checks).
  2. Reserved concurrency on the webui backend Lambda, so a burst that gets
     past WAF (e.g. many IPs) can never run more than RESERVED_CONCURRENCY
     chat turns at once.
  3. Per-request caps on message and history length, enforced server-side in
     lambda_src/webui_backend/handler.py (not here).

Idempotent: finds the web ACL by name and updates it in place; only touches
the distribution config if the web ACL is not already attached.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deploy.common import CLOUDFRONT_REGION, client, load_state, log, save_state, tags_map

WEB_ACL_NAME = "agentcore-mt-webui-waf"
BACKEND_FUNCTION_NAME = "agentcore-mt-webui-backend"
RATE_LIMIT = 100  # requests per IP per evaluation window
RATE_WINDOW_S = 300
RESERVED_CONCURRENCY = 5


def _visibility(metric: str) -> dict:
    return {"SampledRequestsEnabled": True, "CloudWatchMetricsEnabled": True, "MetricName": metric}


def web_acl_rules() -> list[dict]:
    return [
        {
            "Name": "rate-limit-api-per-ip",
            "Priority": 0,
            "Statement": {
                "RateBasedStatement": {
                    "Limit": RATE_LIMIT,
                    "EvaluationWindowSec": RATE_WINDOW_S,
                    "AggregateKeyType": "IP",
                    "ScopeDownStatement": {
                        "ByteMatchStatement": {
                            "SearchString": b"/api/",
                            "FieldToMatch": {"UriPath": {}},
                            "TextTransformations": [{"Priority": 0, "Type": "NONE"}],
                            "PositionalConstraint": "STARTS_WITH",
                        }
                    },
                }
            },
            "Action": {"Block": {"CustomResponse": {"ResponseCode": 429}}},
            "VisibilityConfig": _visibility("agentcore-mt-rate-limit-api"),
        },
        {
            "Name": "aws-managed-common-rules",
            "Priority": 1,
            "Statement": {
                "ManagedRuleGroupStatement": {"VendorName": "AWS", "Name": "AWSManagedRulesCommonRuleSet"}
            },
            "OverrideAction": {"None": {}},
            "VisibilityConfig": _visibility("agentcore-mt-aws-common-rules"),
        },
    ]


def _find_web_acl(waf) -> dict | None:
    kwargs: dict = {"Scope": "CLOUDFRONT", "Limit": 100}
    while True:
        resp = waf.list_web_acls(**kwargs)
        for acl in resp.get("WebACLs", []):
            if acl["Name"] == WEB_ACL_NAME:
                return acl
        marker = resp.get("NextMarker")
        if not marker or not resp.get("WebACLs"):
            return None
        kwargs["NextMarker"] = marker


def ensure_web_acl(waf) -> str:
    existing = _find_web_acl(waf)
    common = {
        "DefaultAction": {"Allow": {}},
        "Rules": web_acl_rules(),
        "VisibilityConfig": _visibility("agentcore-mt-webui-waf"),
        "Description": "Abuse controls for the public AgentCore MT memory demo UI",
    }
    if existing:
        waf.update_web_acl(
            Name=WEB_ACL_NAME, Scope="CLOUDFRONT", Id=existing["Id"], LockToken=existing["LockToken"], **common
        )
        arn = existing["ARN"]
        log(f"updated web ACL: {WEB_ACL_NAME}")
    else:
        resp = waf.create_web_acl(
            Name=WEB_ACL_NAME,
            Scope="CLOUDFRONT",
            Tags=[{"Key": k, "Value": v} for k, v in tags_map().items()],
            **common,
        )
        arn = resp["Summary"]["ARN"]
        log(f"created web ACL: {WEB_ACL_NAME}")
    waf.tag_resource(ResourceARN=arn, Tags=[{"Key": k, "Value": v} for k, v in tags_map().items()])
    return arn


def attach_web_acl(cf, dist_id: str, web_acl_arn: str) -> None:
    resp = cf.get_distribution_config(Id=dist_id)
    cfg = resp["DistributionConfig"]
    if cfg.get("WebACLId") == web_acl_arn:
        log(f"web ACL already attached to distribution {dist_id}")
        return
    cfg["WebACLId"] = web_acl_arn
    cf.update_distribution(Id=dist_id, DistributionConfig=cfg, IfMatch=resp["ETag"])
    log(f"attached web ACL to distribution {dist_id} (propagates in a few minutes)")


def ensure_reserved_concurrency(lam) -> None:
    lam.put_function_concurrency(
        FunctionName=BACKEND_FUNCTION_NAME, ReservedConcurrentExecutions=RESERVED_CONCURRENCY
    )
    log(f"reserved concurrency on {BACKEND_FUNCTION_NAME}: {RESERVED_CONCURRENCY}")


def main() -> None:
    state = load_state()
    webui = state["webui"]
    # CloudFront-scoped WAF resources are always managed from us-east-1.
    waf = client("wafv2", CLOUDFRONT_REGION)
    cf = client("cloudfront")
    lam = client("lambda")

    web_acl_arn = ensure_web_acl(waf)
    attach_web_acl(cf, webui["distribution_id"], web_acl_arn)
    ensure_reserved_concurrency(lam)

    webui["waf_web_acl_arn"] = web_acl_arn
    webui["waf_web_acl_name"] = WEB_ACL_NAME
    webui["backend_reserved_concurrency"] = RESERVED_CONCURRENCY
    save_state(state)
    log("Abuse controls setup complete.")


if __name__ == "__main__":
    main()
