"""Create Omni alerts for the samples: application latency and errors, agent tool failures, and agent quality.

Dry run by default: it resolves the space, lists access profiles, optionally
grounds the SQL alert queries against live data, and prints every CreateAlert
request. Nothing is created until you pass --apply.

    python create_alerts.py --ground                          # dry run with current query values
    python create_alerts.py --apply                           # profile omni-samples-alerts, ALERTS_TOPIC_ARN from .env
    python create_alerts.py --profile-id <id> --sns-topic-arn arn:aws:sns:... --apply   # explicit

The default thresholds are DEMO values sized for the faults the samples inject
(chaos.sh adds 2.5-4 s of latency). Set real thresholds from your own baseline.
"""

import argparse
import json
import os
import pathlib
import sys

import boto3

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "common"))
from omni_query import load_env, run as run_sql  # noqa: E402

# Every rule is SQL and bounds @timestamp relative to NOW(), because an alert has no window of its own.
# FIELD_VALUE SQL needs a named alias (the thresholdField) plus ORDER BY <alias> DESC LIMIT,
# so the evaluated row is the right one.
ERROR_SPANS_SQL = """
SELECT COUNT(*) AS error_spans
FROM traces.default
WHERE `@timestamp` BETWEEN NOW() - INTERVAL '5 MINUTES' AND NOW()
  AND resource['attributes']['service.namespace'] = 'shop'
  AND upper(TRY_CAST(kind AS VARCHAR)) IN ('2', 'SERVER', 'SPAN_KIND_SERVER')
  AND upper(TRY_CAST(status['code'] AS VARCHAR)) IN ('2', 'ERROR', 'STATUS_CODE_ERROR')
ORDER BY error_spans DESC
LIMIT 1
""".strip()

AGENT_TOOL_FAILURES_SQL = """
SELECT COUNT(*) AS failed_tool_calls
FROM traces.default
WHERE `@timestamp` BETWEEN NOW() - INTERVAL '5 MINUTES' AND NOW()
  AND resource['attributes']['service.name'] = 'support-agent'
  AND attributes['gen_ai.operation.name'] = 'execute_tool'
  AND upper(TRY_CAST(status['code'] AS VARCHAR)) IN ('2', 'ERROR', 'STATUS_CODE_ERROR')
ORDER BY failed_tool_calls DESC
LIMIT 1
""".strip()

AGENT_QUALITY_SQL = """
SELECT AVG(CAST(attributes['gen_ai.evaluation.score.value'] AS DOUBLE)) AS avg_score,
       COUNT(*) AS scored
FROM "logs.default"
WHERE `@timestamp` BETWEEN NOW() - INTERVAL '30 MINUTES' AND NOW()
  AND attributes['gen_ai.evaluation.name'] = '{evaluator}'
  AND attributes['error.type'] IS NULL
  AND resource['attributes']['service.name'] IN ('support-agent', 'support-agent.DEFAULT')
HAVING COUNT(*) > 0
ORDER BY avg_score DESC
LIMIT 1
""".strip()
# HAVING COUNT(*) > 0 is a no-data guard, not a second threshold. An aggregate over zero rows
# still returns one row whose AVG is NULL, and the alert reads that as "below threshold"
# (it fired CRITICAL before any scores existed). With the guard, no scores means no rows,
# and noData.treatAs=OK applies.

# Server spans only. OpenTelemetry marks an HTTP CLIENT span as ERROR on any 4xx, so counting
# all spans made the error alert fire forever on the load generator's deliberate 404 lookups.
PAYMENTS_SLOW_SQL = """
SELECT COUNT(*) AS slow_requests
FROM traces.default
WHERE `@timestamp` BETWEEN NOW() - INTERVAL '5 MINUTES' AND NOW()
  AND resource['attributes']['service.name'] = 'payments'
  AND upper(TRY_CAST(kind AS VARCHAR)) IN ('2', 'SERVER', 'SPAN_KIND_SERVER')
  AND TRY_CAST(durationNano AS DOUBLE) > 1e9
ORDER BY slow_requests DESC
LIMIT 1
""".strip()
# Latency is alerted as a COUNT of slow requests (server spans over 1 s). Two other shapes were
# tried and failed in testing:
#   - PromQL histogram_quantile(0.99, sum(rate(traces.span.metrics.duration[5m]))): fired, then stayed
#     CRITICAL more than 10 minutes after the fault ended (the SQL p99 was 5 ms).
#   - SQL approx_percentile_cont(...) AS p99_seconds: never fired, although the same query returned
#     ~2.9 s when run ad hoc during the fault.
# The COUNT shape is the one the error alert proves out. Keep percentiles on dashboards (queries/promql.md).


def alert_definitions(args):
    return [
        {
            "name": "omni-samples.payments-slow-requests",
            "description": "payments requests (server spans) slower than 1 s in the last 5 minutes.",
            "rule": {"telemetryRule": {
                "query": {"language": "SQL", "expression": PAYMENTS_SLOW_SQL},
                "condition": {"thresholdMode": "FIELD_VALUE", "thresholdField": "slow_requests",
                              "comparator": "GT", "criticalThreshold": args.slow_requests},
                "evaluation": {"intervalSeconds": 60, "pendingDurationSeconds": 120,
                               "recoveryDurationSeconds": 120},
                "noData": {"treatAs": "OK"},
            }},
            "states": ["WARNING", "CRITICAL"],
        },
        {
            "name": "omni-samples.shop-error-spans",
            "description": "Failed spans across the shop application in the last 5 minutes.",
            "rule": {"telemetryRule": {
                "query": {"language": "SQL", "expression": ERROR_SPANS_SQL},
                "condition": {"thresholdMode": "FIELD_VALUE", "thresholdField": "error_spans",
                              "comparator": "GT", "criticalThreshold": args.error_spans},
                "evaluation": {"intervalSeconds": 60, "pendingDurationSeconds": 60,
                               "recoveryDurationSeconds": 120},
                "noData": {"treatAs": "OK"},
            }},
            "states": ["WARNING", "CRITICAL"],
        },
        {
            "name": "omni-samples.agent-tool-failures",
            "description": "support-agent tool calls that failed in the last 5 minutes.",
            "rule": {"telemetryRule": {
                "query": {"language": "SQL", "expression": AGENT_TOOL_FAILURES_SQL},
                "condition": {"thresholdMode": "FIELD_VALUE", "thresholdField": "failed_tool_calls",
                              "comparator": "GT", "criticalThreshold": args.tool_failures},
                "evaluation": {"intervalSeconds": 60, "pendingDurationSeconds": 60,
                               "recoveryDurationSeconds": 120},
                "noData": {"treatAs": "OK"},
            }},
            "states": ["WARNING", "CRITICAL"],
        },
        {
            "name": "omni-samples.agent-quality",
            "description": f"Average {args.evaluator} score for support-agent over 30 minutes (online evaluation).",
            "rule": {"telemetryRule": {
                "query": {"language": "SQL", "expression": AGENT_QUALITY_SQL.format(evaluator=args.evaluator)},
                "condition": {"thresholdMode": "FIELD_VALUE", "thresholdField": "avg_score",
                              "comparator": "LT", "criticalThreshold": args.min_quality},
                "evaluation": {"intervalSeconds": 300, "pendingDurationSeconds": 300,
                               "recoveryDurationSeconds": 300},
                # No scored sessions in the window says nothing about quality.
                "noData": {"treatAs": "OK"},
            }},
            "states": ["WARNING", "CRITICAL"],
        },
    ]


def resolve_space(client, region):
    if os.environ.get("OMNI_SPACE_ID"):
        return os.environ["OMNI_SPACE_ID"]
    spaces = [s for s in client.list_spaces()["items"] if s["region"] == region and s["status"] == "ACTIVE"]
    if len(spaces) != 1:
        found = ", ".join(f"{s['name']} ({s['spaceId']})" for s in spaces) or "none"
        sys.exit(f"Expected exactly one ACTIVE space in {region}, found: {found}. Set OMNI_SPACE_ID in .env.")
    print(f"Space: {spaces[0]['name']} ({spaces[0]['spaceId']})")
    return spaces[0]["spaceId"]


def show_profiles(client, space_id):
    print("\nAccess profiles in this space (an alert evaluates its query as one of these):")
    for item in client.list_access_profiles(spaceId=space_id)["items"]:
        detail = client.get_access_profile(spaceId=space_id, profileId=item["profileId"])["accessProfile"]
        print(f"  {item['profileId']:<40} {item['name']:<30} assumeStatus={detail.get('assumeStatus', '?')}")
    print("  Choose one that you can assume (ALLOWED), that has an ALERT trust grant for ALL alerts,\n"
          "  and that confers StartTelemetryQuery and GetTelemetryQueryResults. Without the query\n"
          "  actions, the alert is accepted and then sits at NODATA forever.")


def notification_rules(args, states):
    rules = []
    if args.sns_topic_arn:
        rules.append({"trigger": {"stateValues": states}, "target": {"type": "sns", "arn": args.sns_topic_arn}})
    if args.slack_integration_arn and args.slack_channel:
        rules.append({"trigger": {"stateValues": states},
                      "target": {"type": "slack", "arn": args.slack_integration_arn,
                                 "metadata": {"channel": args.slack_channel.lstrip("#")}}})
    return rules


def main():
    load_env(ROOT / ".env")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile-id")
    p.add_argument("--sns-topic-arn", default=os.environ.get("ALERTS_TOPIC_ARN"),
                   help="default: ALERTS_TOPIC_ARN from .env (set by 00-prerequisites/write_env.sh)")
    p.add_argument("--slack-integration-arn", help="from: aws cloudwatchomni list-integrations")
    p.add_argument("--slack-channel")
    p.add_argument("--evaluator", default="Builtin.Helpfulness")
    p.add_argument("--slow-requests", type=float, default=5, help="payments requests over 1 s per 5 minutes")
    p.add_argument("--error-spans", type=float, default=10)
    p.add_argument("--tool-failures", type=float, default=3)
    p.add_argument("--min-quality", type=float, default=0.6)
    p.add_argument("--only", action="append", help="create only alerts whose name contains this")
    p.add_argument("--ground", action="store_true", help="run the SQL alert queries now and show their values")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()

    region = os.environ["AWS_REGION"]
    client = boto3.client("cloudwatchomni", region_name=region)
    space_id = resolve_space(client, region)
    if not args.profile_id:
        # Default: the profile setup_alert_profile.py creates.
        args.profile_id = next((p["profileId"] for p in client.list_access_profiles(spaceId=space_id)["items"]
                                if p["name"] == "omni-samples-alerts"), None)
        if args.profile_id:
            print(f"Access profile: omni-samples-alerts ({args.profile_id})")
        else:
            print("No 'omni-samples-alerts' profile. Run setup_alert_profile.py --apply, or pass --profile-id.")
            show_profiles(client, space_id)

    alerts = alert_definitions(args)
    if args.only:
        alerts = [a for a in alerts if any(o in a["name"] for o in args.only)]

    existing = {}
    for page in client.get_paginator("list_alerts").paginate(spaceId=space_id):
        existing.update({a["name"]: a["alertId"] for a in page["items"]})

    for alert in alerts:
        query = alert["rule"]["telemetryRule"]["query"]
        request = {
            "spaceId": space_id,
            "profileId": args.profile_id or "<choose --profile-id>",
            "name": alert["name"],
            "description": alert["description"],
            "rule": alert["rule"],
            "tags": {"project": "omni-samples"},
        }
        rules = notification_rules(args, alert["states"])
        if rules:
            request["notificationRules"] = rules

        print(f"\n=== {alert['name']}")
        if args.ground:
            if query["language"] == "SQL":
                rows, _ = run_sql(query["expression"], 5)
                print(f"current value: {rows}")
            else:
                print("current value: check this PromQL in Omni > Explore (the ad hoc query API is SQL-only)")
        print(json.dumps(request, indent=2))

        if args.apply:
            if not args.profile_id:
                sys.exit("--apply needs --profile-id")
            if alert["name"] in existing:  # idempotent: re-running updates in place
                update = {k: v for k, v in request.items() if k != "tags"}
                update["alertId"] = existing[alert["name"]]
                update.setdefault("notificationRules", [])
                client.update_alert(**update)
                print(f"updated: {existing[alert['name']]}")
            else:
                created = client.create_alert(**request)["alert"]
                print(f"created: {created['alertId']}  {created['alertArn']}")

    if not args.apply:
        print("\nDRY RUN. Nothing was created. Re-run with --apply.")


if __name__ == "__main__":
    main()
