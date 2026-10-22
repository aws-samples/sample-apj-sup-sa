"""Step 12: observability -- X-Ray active tracing on all 3 Lambdas, the
AgentCore Runtime's vended traces and logs, and one CloudWatch dashboard
covering Lambda errors/duration/invocations, the Runtime, the Gateway's own
per-tool metrics, and Bedrock InvokeModel latency/errors. Run it LAST
(deploy_all does): it covers every resource the other steps create.

ACCOUNT-WIDE SETTING: the Runtime's vended TRACES can only be delivered to
X-Ray once X-Ray's trace segment destination is CloudWatch Logs
("Transaction Search", also what CloudWatch GenAI Observability reads). This
step turns it on for the deploy region (with the logs resource policy it
needs) and leaves span indexing at 1%, the free tier. It applies to every
X-Ray trace in the account/region, not just this demo -- check before
running it in an account with other X-Ray traffic. Revert with
`aws xray update-trace-segment-destination --destination XRay`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.tenants import REGION
from deploy.common import account_id, client, load_state, log, save_state, wait_until

FUNCTION_NAMES = ["agentcore-mt-interceptor", "agentcore-mt-tools", "agentcore-mt-webui-backend"]
DASHBOARD_NAME = "agentcore-mt-memory-demo"
LOG_RETENTION_DAYS = 14
TAGS = {"project": "agentcore-mt-memory-demo"}


def enable_transaction_search(logs, xray, acct: str) -> None:
    logs.put_resource_policy(
        policyName="agentcore-mt-xray-transaction-search",
        policyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "TransactionSearchXRayAccess",
                        "Effect": "Allow",
                        "Principal": {"Service": "xray.amazonaws.com"},
                        "Action": "logs:PutLogEvents",
                        "Resource": [
                            f"arn:aws:logs:{REGION}:{acct}:log-group:aws/spans:*",
                            f"arn:aws:logs:{REGION}:{acct}:log-group:/aws/application-signals/data:*",
                        ],
                        "Condition": {
                            "ArnLike": {"aws:SourceArn": f"arn:aws:xray:{REGION}:{acct}:*"},
                            "StringEquals": {"aws:SourceAccount": acct},
                        },
                    }
                ],
            }
        ),
    )
    if xray.get_trace_segment_destination()["Destination"] != "CloudWatchLogs":
        xray.update_trace_segment_destination(Destination="CloudWatchLogs")
        log("X-Ray trace segment destination -> CloudWatchLogs (Transaction Search on)")
    else:
        log("Transaction Search already on")
    # The switch takes a few minutes (PENDING -> ACTIVE); X-Ray deliveries
    # are rejected until it is ACTIVE.
    wait_until(
        lambda: xray.get_trace_segment_destination()["Status"] == "ACTIVE",
        timeout_s=900, interval_s=15, what="Transaction Search ACTIVE",
    )
    xray.update_indexing_rule(Name="Default", Rule={"Probabilistic": {"DesiredSamplingPercentage": 1}})


def _ensure_log_group(logs, name: str) -> str:
    try:
        logs.create_log_group(logGroupName=name, tags=TAGS)
    except logs.exceptions.ResourceAlreadyExistsException:
        pass
    logs.put_retention_policy(logGroupName=name, retentionInDays=LOG_RETENTION_DAYS)
    return f"arn:aws:logs:{REGION}:{account_id()}:log-group:{name}"


def _put_tagged(logs, fn, **kwargs) -> dict:
    """Delivery put_* calls accept tags only when creating, not updating."""
    try:
        return fn(tags=TAGS, **kwargs)
    except logs.exceptions.ConflictException:
        return fn(**kwargs)


def wire_runtime_deliveries(logs, runtime: dict) -> None:
    """Vended Runtime observability: TRACES -> X-Ray, APPLICATION_LOGS and
    USAGE_LOGS (per-session vCPU/memory, i.e. what the Runtime bills) ->
    their own log groups."""
    rid = runtime["agent_runtime_id"]
    targets = {
        "TRACES": ("agentcore-mt-runtime-xray", {"deliveryDestinationType": "XRAY"}),
        "APPLICATION_LOGS": (
            "agentcore-mt-runtime-app-logs",
            {"deliveryDestinationConfiguration": {"destinationResourceArn": _ensure_log_group(
                logs, f"/aws/vendedlogs/bedrock-agentcore/runtime/{rid}/application")}},
        ),
        "USAGE_LOGS": (
            "agentcore-mt-runtime-usage-logs",
            {"deliveryDestinationConfiguration": {"destinationResourceArn": _ensure_log_group(
                logs, f"/aws/vendedlogs/bedrock-agentcore/runtime/{rid}/usage")}},
        ),
    }
    existing = {d["deliverySourceName"] for d in logs.describe_deliveries().get("deliveries", [])}
    for log_type, (dest_name, dest_kwargs) in targets.items():
        source_name = f"agentcore-mt-runtime-{log_type.lower().replace('_', '-')}"
        _put_tagged(logs, logs.put_delivery_source, name=source_name, resourceArn=runtime["agent_runtime_arn"], logType=log_type)
        dest = _put_tagged(logs, logs.put_delivery_destination, name=dest_name, **dest_kwargs)["deliveryDestination"]
        if source_name not in existing:
            logs.create_delivery(deliverySourceName=source_name, deliveryDestinationArn=dest["arn"], tags=TAGS)
        log(f"runtime {log_type} -> {dest_name}")


def runtime_widgets(region: str, runtime: dict, y: int) -> list:
    dims = [
        "Resource", runtime["agent_runtime_arn"],
        "Operation", "InvokeAgentRuntime",
        "Name", f"{runtime['agent_runtime_arn'].split('/')[-1].rsplit('-', 1)[0]}::DEFAULT",
    ]
    def metric(name: str, stat: str) -> list:
        return ["AWS/Bedrock-AgentCore", name, *dims, {"stat": stat}]

    return [
        {
            "type": "metric", "x": 0, "y": y, "width": 6, "height": 6,
            "properties": {
                "title": "AgentCore Runtime: invocations / sessions / errors",
                "metrics": [metric("Invocations", "Sum"), metric("Sessions", "Sum"),
                            metric("SystemErrors", "Sum"), metric("UserErrors", "Sum"), metric("Throttles", "Sum")],
                "region": region, "period": 300,
            },
        },
        {
            "type": "metric", "x": 6, "y": y, "width": 6, "height": 6,
            "properties": {
                "title": "AgentCore Runtime: latency (ms)",
                "metrics": [metric("Latency", "Average"), metric("Latency", "p99")],
                "region": region, "period": 300,
            },
        },
        {
            "type": "log", "x": 0, "y": y + 6, "width": 12, "height": 6,
            "properties": {
                "title": "AgentCore Runtime: turns by brand (agent's own structured logs)",
                "query": f"SOURCE '{runtime['log_group']}' | filter event = \"turn\" "
                         "| stats count(*) as turns, avg(latency_ms) as avg_ms, sum(cache_read_tokens) as cache_read "
                         "by tenant_id, agent_id",
                "region": region, "view": "table",
            },
        },
    ]


def gateway_widget(region: str, gateway_arn: str, y: int) -> dict:
    return {
        "type": "metric", "x": 0, "y": y, "width": 12, "height": 6,
        "properties": {
            "title": "AgentCore Gateway: tools/call latency by tool (ms)",
            "metrics": [[{
                "expression": f"SEARCH('{{AWS/Bedrock-AgentCore,Method,Name,Operation,Protocol,Resource}} "
                              f"MetricName=\"Latency\" Operation=\"InvokeGateway\" Resource=\"{gateway_arn}\"', 'Average', 300)",
                "id": "gw", "label": "",
            }]],
            "region": region, "period": 300,
        },
    }


def enable_xray(lam, iam, acct: str) -> None:
    for fn in FUNCTION_NAMES:
        lam.update_function_configuration(FunctionName=fn, TracingConfig={"Mode": "Active"})
        log(f"enabled X-Ray active tracing on {fn}")

    # Each function's execution role needs xray:PutTraceSegments/PutTelemetryRecords.
    role_names = ["agentcore-mt-interceptor-role", "agentcore-mt-tools-role", "agentcore-mt-webui-backend-role"]
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                "Resource": "*",  # X-Ray write-only telemetry actions do not support resource-level scoping
            }
        ],
    }
    for role_name in role_names:
        iam.put_role_policy(RoleName=role_name, PolicyName="xray-write", PolicyDocument=json.dumps(policy))
        log(f"  granted xray:PutTraceSegments/PutTelemetryRecords -> {role_name}")


def build_dashboard(region: str, acct: str, state: dict) -> dict:
    widgets = []
    x = 0
    for fn in FUNCTION_NAMES:
        widgets.append(
            {
                "type": "metric",
                "x": x % 12,
                "y": (x // 12) * 6,
                "width": 6,
                "height": 6,
                "properties": {
                    "title": f"{fn}: invocations/errors",
                    "metrics": [
                        ["AWS/Lambda", "Invocations", "FunctionName", fn, {"stat": "Sum"}],
                        ["AWS/Lambda", "Errors", "FunctionName", fn, {"stat": "Sum"}],
                    ],
                    "region": region,
                    "period": 300,
                },
            }
        )
        x += 6
        widgets.append(
            {
                "type": "metric",
                "x": x % 12,
                "y": (x // 12) * 6,
                "width": 6,
                "height": 6,
                "properties": {
                    "title": f"{fn}: duration (ms)",
                    "metrics": [["AWS/Lambda", "Duration", "FunctionName", fn, {"stat": "p99"}]],
                    "region": region,
                    "period": 300,
                },
            }
        )
        x += 6

    widgets.append(
        {
            "type": "metric",
            "x": 0,
            "y": 18,
            "width": 12,
            "height": 6,
            "properties": {
                "title": "Bedrock InvokeModel latency/errors (all models, whole account)",
                "metrics": [
                    ["AWS/Bedrock", "Invocations", {"stat": "Sum"}],
                    ["AWS/Bedrock", "InvocationLatency", {"stat": "Average"}],
                    ["AWS/Bedrock", "InvocationClientErrors", {"stat": "Sum"}],
                ],
                "region": region,
                "period": 300,
            },
        }
    )
    y = 24
    widgets.append(gateway_widget(region, state["gateway"]["gateway_arn"], y))
    y += 6
    if state.get("runtime"):
        widgets.extend(runtime_widgets(region, state["runtime"], y))
        y += 12
    widgets.append(
        {
            "type": "text",
            "x": 0,
            "y": y,
            "width": 12,
            "height": 3,
            "properties": {
                "markdown": "### AgentCore Multi-Tenant Memory Demo\n"
                f"Traces: [X-Ray / Transaction Search](https://console.aws.amazon.com/cloudwatch/home?region={REGION}#xray:traces) "
                "for the 3 Lambdas and the AgentCore Runtime (vended TRACES), and "
                f"[GenAI Observability](https://console.aws.amazon.com/cloudwatch/home?region={REGION}#gen-ai-observability/agent-core) "
                "for the Runtime agent. Runtime usage logs (per-session vCPU/memory) are in "
                "/aws/vendedlogs/bedrock-agentcore/runtime/<id>/usage.",
            },
        }
    )
    return {"widgets": widgets}


def main() -> None:
    state = load_state()
    acct = account_id()
    region = REGION
    lam = client("lambda")
    iam = client("iam")
    cw = client("cloudwatch")

    enable_xray(lam, iam, acct)
    logs = client("logs")
    enable_transaction_search(logs, client("xray"), acct)
    if state.get("runtime"):
        wire_runtime_deliveries(logs, state["runtime"])

    dashboard_body = build_dashboard(region, acct, state)
    cw.put_dashboard(DashboardName=DASHBOARD_NAME, DashboardBody=json.dumps(dashboard_body))
    dashboard_url = (
        f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#dashboards/dashboard/{DASHBOARD_NAME}"
    )
    log(f"created/updated CloudWatch dashboard: {DASHBOARD_NAME}")
    log(f"  {dashboard_url}")

    state["observability"] = {
        "dashboard_name": DASHBOARD_NAME,
        "dashboard_url": dashboard_url,
        "xray_enabled": True,
        "transaction_search": True,
        "runtime_deliveries": bool(state.get("runtime")),
    }
    save_state(state)
    log("Observability setup complete.")


if __name__ == "__main__":
    main()
