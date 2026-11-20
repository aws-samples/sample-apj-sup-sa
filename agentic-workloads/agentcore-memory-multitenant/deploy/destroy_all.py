"""Tears down every resource created by this demo, in reverse dependency
order. SAFE BY DEFAULT: without --yes, this only prints what it would do
(dry run). Requires --yes to actually delete anything.

Usage:
    python3 deploy/destroy_all.py            # dry run (prints plan only)
    python3 deploy/destroy_all.py --yes      # actually tears everything down

Order: CloudFront distribution (disable, wait, delete) -> WAF web ACL -> S3
bucket (empty, delete) -> webui backend Lambda -> AgentCore Runtime (+ ECR repo, role, log
deliveries) -> S3 Vectors KB -> Gateway target -> Gateway -> Lambda
functions -> Memory -> IAM (inline policies then roles) -> SSM parameters
-> Cognito (domain -> resource server -> user pool, which cascades to
delete its app clients).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deploy.common import CLOUDFRONT_REGION, client, load_state, log, save_state

DRY_RUN = "--yes" not in sys.argv


def act(description: str, fn) -> None:
    if DRY_RUN:
        log(f"[DRY RUN] would: {description}")
        return
    log(f"{description} ...")
    try:
        fn()
        log("  done")
    except Exception as exc:  # noqa: BLE001
        log(f"  WARNING: {exc}")


def _wait_for_gone(check_fn, what: str, timeout_s: int = 120, interval_s: int = 5) -> None:
    """Poll check_fn() (a describe/get call) until it raises (resource gone)
    or times out. Used between dependent deletes (e.g. target before
    gateway) so we don't race an async deletion."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            resp = check_fn()
            status = resp.get("status")
            log(f"  waiting for {what}... current status={status}")
        except Exception:
            log(f"  {what} confirmed gone")
            return
        time.sleep(interval_s)
    log(f"  WARNING: timed out waiting for {what}; proceeding anyway")


def _delete_role_fully(iam, role_name: str) -> None:
    for pol in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
        iam.delete_role_policy(RoleName=role_name, PolicyName=pol)
    for pol in iam.list_attached_role_policies(RoleName=role_name).get("AttachedPolicies", []):
        iam.detach_role_policy(RoleName=role_name, PolicyArn=pol["PolicyArn"])
    iam.delete_role(RoleName=role_name)


def main() -> None:
    state = load_state()
    if DRY_RUN:
        log("*** DRY RUN MODE (default) -- pass --yes to actually delete anything ***\n")

    ctl = client("bedrock-agentcore-control")
    lam = client("lambda")
    iam = client("iam")
    idp = client("cognito-idp")
    s3 = client("s3")
    cf = client("cloudfront")
    ssm = client("ssm")

    webui = state.get("webui", {})
    dist_id = webui.get("distribution_id")
    if dist_id:
        def disable_and_delete_distribution():
            cfg_resp = cf.get_distribution_config(Id=dist_id)
            etag = cfg_resp["ETag"]
            dist_cfg = cfg_resp["DistributionConfig"]
            if dist_cfg["Enabled"]:
                dist_cfg["Enabled"] = False
                upd = cf.update_distribution(Id=dist_id, DistributionConfig=dist_cfg, IfMatch=etag)
                etag = upd["ETag"]
                log("  disabled distribution, waiting for it to finish deploying (can take ~15min)...")
                waiter = cf.get_waiter("distribution_deployed")
                waiter.wait(Id=dist_id, WaiterConfig={"Delay": 30, "MaxAttempts": 60})
                etag = cf.get_distribution_config(Id=dist_id)["ETag"]
            cf.delete_distribution(Id=dist_id, IfMatch=etag)

        act(f"disable + delete CloudFront distribution {dist_id} (slow: can take ~15min)", disable_and_delete_distribution)

    # The web ACL can only be deleted once no distribution references it,
    # i.e. after the distribution above is gone.
    waf_name = webui.get("waf_web_acl_name")
    if waf_name:
        waf = client("wafv2", CLOUDFRONT_REGION)

        def delete_web_acl():
            for acl in waf.list_web_acls(Scope="CLOUDFRONT", Limit=100).get("WebACLs", []):
                if acl["Name"] == waf_name:
                    waf.delete_web_acl(Name=waf_name, Scope="CLOUDFRONT", Id=acl["Id"], LockToken=acl["LockToken"])

        act(f"delete WAF web ACL {waf_name} (CLOUDFRONT scope)", delete_web_acl)

    bucket = webui.get("bucket")
    if bucket:
        def empty_and_delete_bucket():
            paginator = s3.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=bucket):
                to_delete = [{"Key": v["Key"], "VersionId": v["VersionId"]} for v in page.get("Versions", [])]
                to_delete += [{"Key": v["Key"], "VersionId": v["VersionId"]} for v in page.get("DeleteMarkers", [])]
                if to_delete:
                    s3.delete_objects(Bucket=bucket, Delete={"Objects": to_delete})
            s3.delete_bucket(Bucket=bucket)

        act(f"empty + delete S3 bucket {bucket}", empty_and_delete_bucket)

    backend_fn_arn = webui.get("backend_function_arn")
    if backend_fn_arn:
        act("delete web UI backend Lambda function", lambda: lam.delete_function(FunctionName="agentcore-mt-webui-backend"))
        act(
            "delete web UI backend IAM role (incl. inline policies)",
            lambda: _delete_role_fully(iam, "agentcore-mt-webui-backend-role"),
        )

    if webui.get("guardrail_id"):
        act(
            f"delete Bedrock guardrail {webui['guardrail_id']}",
            lambda: client("bedrock").delete_guardrail(guardrailIdentifier=webui["guardrail_id"]),
        )

    dashboard = state.get("observability", {}).get("dashboard_name")
    if dashboard:
        act(
            f"delete CloudWatch dashboard {dashboard}",
            lambda: client("cloudwatch").delete_dashboards(DashboardNames=[dashboard]),
        )

    if state.get("cognito"):
        for key in state["cognito"].get("clients", {}):
            if state["cognito"]["clients"][key].get("tenant_id") is None:
                continue
            param = f"/agentcore-mt-demo/cognito-secret/{key.replace(':', '-')}"
            act(f"delete SSM parameter {param}", lambda p=param: ssm.delete_parameter(Name=p))

    rt = state.get("runtime", {})
    if rt.get("agent_runtime_id"):
        logs = client("logs")
        for src in ("traces", "application-logs", "usage-logs"):
            name = f"agentcore-mt-runtime-{src}"

            def delete_delivery(n=name):
                for d in logs.describe_deliveries().get("deliveries", []):
                    if d["deliverySourceName"] == n:
                        logs.delete_delivery(id=d["id"])
                logs.delete_delivery_source(name=n)

            act(f"delete Runtime log delivery + source {name}", delete_delivery)
        for dest in ("agentcore-mt-runtime-xray", "agentcore-mt-runtime-app-logs", "agentcore-mt-runtime-usage-logs"):
            act(f"delete log delivery destination {dest}", lambda d=dest: logs.delete_delivery_destination(name=d))
        act(
            f"delete AgentCore Runtime {rt['agent_runtime_id']}",
            lambda: ctl.delete_agent_runtime(agentRuntimeId=rt["agent_runtime_id"]),
        )
        act(
            f"delete ECR repo {rt['ecr_repo']} (incl. images)",
            lambda: client("ecr").delete_repository(repositoryName=rt["ecr_repo"], force=True),
        )
        act("delete IAM role agentcore-mt-runtime-role", lambda: _delete_role_fully(iam, "agentcore-mt-runtime-role"))
        log("  note: X-Ray Transaction Search (account-wide, deploy/12) is left on; revert with "
            "`aws xray update-trace-segment-destination --destination XRay` if nothing else uses it")

    kb = state.get("kb", {})
    if kb.get("vector_bucket"):
        s3v = client("s3vectors")

        def delete_kb():
            for idx in s3v.list_indexes(vectorBucketName=kb["vector_bucket"]).get("indexes", []):
                s3v.delete_index(vectorBucketName=kb["vector_bucket"], indexName=idx["indexName"])
            s3v.delete_vector_bucket(vectorBucketName=kb["vector_bucket"])

        act(f"delete S3 Vectors bucket {kb['vector_bucket']} and its per-brand indexes", delete_kb)

    gw = state.get("gateway", {})
    if gw.get("gateway_id") and gw.get("target_id"):
        act(
            f"delete gateway target {gw['target_id']} on gateway {gw['gateway_id']}",
            lambda: ctl.delete_gateway_target(gatewayIdentifier=gw["gateway_id"], targetId=gw["target_id"]),
        )
        if not DRY_RUN:
            _wait_for_gone(
                lambda: ctl.get_gateway_target(gatewayIdentifier=gw["gateway_id"], targetId=gw["target_id"]),
                what="gateway target deletion",
            )
    if gw.get("gateway_id"):
        act(f"delete gateway {gw['gateway_id']}", lambda: ctl.delete_gateway(gatewayIdentifier=gw["gateway_id"]))
        if not DRY_RUN:
            _wait_for_gone(lambda: ctl.get_gateway(gatewayIdentifier=gw["gateway_id"]), what="gateway deletion")

    lambdas = state.get("lambdas", {})
    for name in ("agentcore-mt-interceptor", "agentcore-mt-tools"):
        act(f"delete lambda function {name}", lambda n=name: lam.delete_function(FunctionName=n))

    mem = state.get("memory", {})
    if mem.get("memory_id"):
        act(f"delete memory {mem['memory_id']}", lambda: ctl.delete_memory(memoryId=mem["memory_id"]))

    iam_state = state.get("iam", {})

    role_names = [
        "agentcore-mt-gateway-role",
        "agentcore-mt-memory-exec-role",
        "agentcore-mt-interceptor-role",
        "agentcore-mt-tools-role",
    ] + [f"agentcore-mt-tenant-access-{t}" for t in iam_state.get("tenant_access_role_arns", {})]
    for role_name in role_names:
        act(f"delete IAM role (incl. inline policies) {role_name}", lambda r=role_name: _delete_role_fully(iam, r))

    cognito = state.get("cognito", {})
    pool_id = cognito.get("user_pool_id")
    domain = cognito.get("domain")
    if pool_id and domain:
        act(
            f"delete cognito domain {domain}",
            lambda: idp.delete_user_pool_domain(Domain=domain, UserPoolId=pool_id),
        )
        if not DRY_RUN:
            log("  waiting for domain deletion to finish propagating...")
            time.sleep(20)
    if pool_id:
        rsid = cognito.get("resource_server_id")
        if rsid:
            act(
                f"delete cognito resource server {rsid}",
                lambda: idp.delete_resource_server(UserPoolId=pool_id, Identifier=rsid),
            )
        act(
            f"delete cognito user pool {pool_id} (cascades to its app clients)",
            lambda: idp.delete_user_pool(UserPoolId=pool_id),
        )

    if DRY_RUN:
        log("\nDry run complete. No resources were modified. Re-run with --yes to actually tear down.")
    else:
        log("\nTeardown complete. Clearing deploy/state.json.")
        save_state({})


if __name__ == "__main__":
    main()
