"""End-to-end smoke test for the CloudWatch Omni samples. Read-only, except for the --live traffic it sends.

Run from the repo root, with member-account credentials (source common/assume-member-role.sh):

    python common/smoke_test.py            # checks infrastructure, access, and recent data
    python common/smoke_test.py --live     # also sends a shop request and an agent turn and waits for them

Exits non-zero if any check fails.
"""

import argparse
import datetime
import http.client
import os
import pathlib
import re
import subprocess  # nosec B404 - the live smoke test executes a fixed local script
import sys
import time

import boto3

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "common"))
from omni_query import load_env, run as run_sql  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  - ' + detail if detail else ''}", flush=True)
    return ok


def scalar(sql, column):
    rows, _ = run_sql(sql, 50)
    return rows


def window(minutes):
    return f"`@timestamp` BETWEEN NOW() - INTERVAL '{minutes} MINUTES' AND NOW()"


def main():
    load_env(ROOT / ".env")
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true", help="send fresh traffic and wait for it to appear")
    p.add_argument("--recent-minutes", type=int, default=180, help="how far back 'recent data' checks look")
    args = p.parse_args()

    region = os.environ["AWS_REGION"]
    account = boto3.client("sts").get_caller_identity()["Account"]
    print(f"Account {account}, Region {region}\n")
    omni = boto3.client("cloudwatchomni", region_name=region)
    recent = window(args.recent_minutes)

    print("Infrastructure (step 0)")
    cfn = boto3.client("cloudformation", region_name=region)
    for stack in ("OmniSamplesPrereqs", "OmniSamplesAgentEvaluation"):
        try:
            status = cfn.describe_stacks(StackName=stack)["Stacks"][0]["StackStatus"]
        except Exception as exc:  # stack missing
            status = type(exc).__name__
        check(f"stack {stack}", status.endswith("_COMPLETE") and "ROLLBACK" not in status, status)
    spaces = [s for s in omni.list_spaces()["items"] if s["region"] == region]
    space = spaces[0] if spaces else None
    check("space ACTIVE in this Region", space and space["status"] == "ACTIVE",
          f"{space['name']} {space['spaceId']}" if space else "no space")
    if not space:
        return finish()
    sid = space["spaceId"]
    di = boto3.client("observabilityadmin", region_name=region).list_dataset_integrations()["DatasetIntegrationSummaries"]
    check("dataset integration forwarding logs/traces", len(di) == 1)
    dest = boto3.client("xray", region_name=region).get_trace_segment_destination()
    check("Transaction Search ACTIVE", dest["Destination"] == "CloudWatchLogs" and dest["Status"] == "ACTIVE",
          f"{dest['Destination']}/{dest['Status']}")

    print("\nAccess (SSO best practice)")
    grants = []
    for page in omni.get_paginator("list_access_grants").paginate(spaceId=sid):
        grants += page["items"]
    def has(ptype, pid, perm):
        return any(g["principal"]["principalType"] == ptype and g["principal"].get("principalId") == pid
                   and g["permission"] == perm for g in grants)
    check("omni-space-admins group has SPACE_ADMIN",
          has("IDC_GROUP", os.environ.get("OMNI_ADMIN_GROUP_ID"), "SPACE_ADMIN"))
    check("omni-viewers group has READ", has("IDC_GROUP", os.environ.get("OMNI_VIEWER_GROUP_ID"), "READ"))
    people_iam = [g for g in grants if g["principal"]["principalType"] in ("IAM_USER", "IAM_ROOT")]
    check("no per-person IAM user grants (people use groups)", not people_iam, f"{len(people_iam)} found")

    print("\nSample 02: microservices")
    rows = scalar(
        f"SELECT resource['attributes']['service.name'] AS service, COUNT(*) AS n, "  # nosec B608 - typed time window
        f"COUNT(DISTINCT spanId) AS d FROM traces.default WHERE {recent} "
        "AND resource['attributes']['service.namespace'] = 'shop' "
        "GROUP BY resource['attributes']['service.name']",
        "n",
    )
    by = {r["service"]: (int(r["n"]), int(r["d"])) for r in rows}
    for svc in ("frontend", "orders", "payments"):
        check(f"spans from {svc}", by.get(svc, (0, 0))[0] > 0, f"{by.get(svc, (0, 0))[0]} spans")
    dup = {s: n - d for s, (n, d) in by.items() if n != d}
    check("no duplicate span records (FastAPI auto_configure off)", not dup, str(dup) if dup else "")
    shop_log_group = os.environ.get("SHOP_LOG_GROUP", "/omni-samples/shop")
    if not re.fullmatch(r"[.\-_/#A-Za-z0-9]+", shop_log_group):
        sys.exit("SHOP_LOG_GROUP contains characters that AWS log-group names do not allow")
    rows = scalar(
        f"SELECT COUNT(*) AS n FROM logs.default WHERE {recent} "  # nosec B608 - validated AWS log-group naming
        f"AND `@logGroupName` = '{shop_log_group}'",
        "n",
    )
    check("shop logs forwarded", rows and int(rows[0]["n"]) > 0, f"{rows[0]['n'] if rows else 0} records")

    print("\nSample 01: AI agent")
    rows = scalar(
        f"SELECT COUNT(*) AS n FROM traces.default WHERE {recent} "  # nosec B608 - typed time window
        "AND resource['attributes']['service.name'] = 'support-agent' "
        "AND attributes['gen_ai.operation.name'] = 'invoke_agent'",
        "n",
    )
    check("agent turns traced", rows and int(rows[0]["n"]) > 0, f"{rows[0]['n'] if rows else 0} turns")
    ac = boto3.client("bedrock-agentcore-control", region_name=region)
    cfgs = ac.list_online_evaluation_configs()["onlineEvaluationConfigs"]
    check("online evaluation ENABLED", any(c.get("executionStatus") == "ENABLED" for c in cfgs),
          ", ".join(f"{c['onlineEvaluationConfigName']}={c.get('executionStatus')}" for c in cfgs))
    rows = scalar(
        f"SELECT attributes['gen_ai.evaluation.name'] AS e, COUNT(*) AS n FROM \"logs.default\" "  # nosec B608 - typed time window
        f"WHERE {window(max(args.recent_minutes, 360))} AND attributes['gen_ai.evaluation.name'] IS NOT NULL "
        "AND attributes['error.type'] IS NULL "
        "AND resource['attributes']['service.name'] IN ('support-agent', 'support-agent.DEFAULT') "
        "GROUP BY attributes['gen_ai.evaluation.name']",
        "n",
    )
    scores = {r["e"]: int(r["n"]) for r in rows}
    for evaluator in ("Builtin.Helpfulness", "ScopeAdherence"):
        check(f"evaluation scores: {evaluator}", scores.get(evaluator, 0) > 0, f"{scores.get(evaluator, 0)} scored")

    print("\nSample 03: agent + app correlation")
    rows = scalar(
        f"SELECT COUNT(DISTINCT traceId) AS n FROM traces.default WHERE {recent} "  # nosec B608 - typed time window
        "AND resource['attributes']['service.name'] = 'payments' "
        f"AND traceId IN (SELECT DISTINCT traceId FROM traces.default WHERE {recent} "
        "AND resource['attributes']['service.name'] = 'support-agent')",
        "n",
    )
    check("agent traces continue into payments", rows and int(rows[0]["n"]) > 0, f"{rows[0]['n'] if rows else 0} traces")

    print("\nSample 04: alerts")
    alerts = omni.list_alerts(spaceId=sid)["items"]
    ours = [a for a in alerts if a["name"].startswith("omni-samples.")]
    check("4 sample alerts exist", len(ours) == 4, f"{len(ours)} found")
    for a in ours:
        state = omni.get_alert(spaceId=sid, alertId=a["alertId"])["alert"].get("state", {}).get("value")
        check(f"alert {a['name']} evaluating", state in ("OK", "WARNING", "CRITICAL"), state or "no state")
    profiles = [x for x in omni.list_access_profiles(spaceId=sid)["items"] if x["name"] == "omni-samples-alerts"]
    check("alert access profile", len(profiles) == 1)

    if args.live:
        live(region)
    finish()


def poll(sql, timeout_s=420):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        rows, _ = run_sql(sql, 5)
        if rows and int(rows[0]["n"]) > 0:
            return True, int(time.time() - (deadline - timeout_s))
        time.sleep(20)
    return False, timeout_s


def live(region):
    print("\nLive: fresh traffic reaches the space")
    start = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    since = f"`@timestamp` BETWEEN to_timestamp_nanos('{start}') AND NOW()"
    conn = http.client.HTTPConnection("localhost", 8000, timeout=10)
    try:
        conn.request("GET", "/orders/ORD-1001")
        check("shop responds (frontend :8000)", conn.getresponse().status == 200)
    except Exception as exc:
        check("shop responds (frontend :8000)", False, f"{exc}. Start it with 02-microservices-apm/up.sh")
    finally:
        conn.close()
    try:
        agent = subprocess.run(  # nosec B603 - fixed local script and constant argument vector
            ["./run.sh", "--sessions", "1", "--kind", "in_scope"], cwd=ROOT / "01-agent-observability",
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        check("agent turn runs", False, "timed out after 300s")
    else:
        check("agent turn runs", agent.returncode == 0 and "agent error" not in agent.stdout,
              f"exit {agent.returncode}")
    ok, secs = poll(
        f"SELECT COUNT(*) AS n FROM traces.default WHERE {since} "  # nosec B608 - generated UTC timestamp
        "AND resource['attributes']['service.name'] = 'frontend'"
    )
    check("new shop spans queryable", ok, f"after ~{secs}s")
    ok, secs = poll(
        f"SELECT COUNT(*) AS n FROM traces.default WHERE {since} "  # nosec B608 - generated UTC timestamp
        "AND resource['attributes']['service.name'] = 'support-agent'"
    )
    check("new agent spans queryable", ok, f"after ~{secs}s")


def finish():
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
