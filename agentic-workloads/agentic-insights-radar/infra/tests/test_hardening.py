# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Controls added after the R1 red-team review: agent function URLs, run bookkeeping, alarms, edge
logging, cost and backup options, sign-in flows and the model scope."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from aws_cdk.assertions import Match, Template

from insights_radar_infra import agents, monitoring, nag, scanners
from insights_radar_infra.config import MODEL_KEYS, DeployConfig

from .conftest import Synth, build, synth

ROOT = Path(__file__).resolve().parents[2]


def _statements(s: Synth, role_prefix: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for policy in s.props("AWS::IAM::Policy"):
        if any(json.dumps(r).find(role_prefix) >= 0 for r in policy["Roles"]):
            out += policy["PolicyDocument"]["Statement"]
    return out


def _actions(statements: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for st in statements:
        a = st["Action"]
        found |= set(a if isinstance(a, list) else [a])
    return found


# ---- Agent routes: IAM-authenticated function URLs, not the public HTTP API -----------------------


def test_agent_routes_are_function_urls_with_iam_auth_and_no_resource_policy(
    default: Synth,
) -> None:
    urls = default.props("AWS::Lambda::Url")
    assert len(urls) == 2 and {u["AuthType"] for u in urls} == {"AWS_IAM"}
    targets = {json.dumps(u["TargetFunctionArn"]) for u in urls}
    assert any("BackendAgentTools" in t for t in targets) and any(
        "BackendChatUsage" in t for t in targets
    )
    # No resource policy opens a URL to anyone: a caller from another account is refused by Lambda.
    perms = default.props("AWS::Lambda::Permission")
    assert not [
        p
        for p in perms
        if "FunctionUrl" in json.dumps(p) or p["Action"].endswith("InvokeFunctionUrl")
    ]


def test_sr08_the_api_function_cannot_act_as_an_agent_gate(default: Synth) -> None:
    env = {
        k: v["Properties"].get("Environment", {}).get("Variables", {})
        for k, v in default.resources("AWS::Lambda::Function").items()
    }
    (api,) = [v for k, v in env.items() if k.startswith("BackendApiHandler")]
    assert (
        "AIR_MODULE_RUNTIME_ROLE_ARN" not in api
        and "AIR_CHAT_RUNTIME_ROLE_ARN" not in api
    )
    (tools,) = [v for k, v in env.items() if k.startswith("BackendAgentTools")]
    (usage,) = [v for k, v in env.items() if k.startswith("BackendChatUsage")]
    assert (
        "AIR_MODULE_RUNTIME_ROLE_ARN" in tools and "AIR_CHAT_RUNTIME_ROLE_ARN" in usage
    )


def test_each_runtime_may_call_only_its_own_function_url(default: Synth) -> None:
    for runtime, own, other in (
        ("AgentsModuleRuntime", "BackendAgentTools", "BackendChatUsage"),
        ("AgentsChatRuntime", "BackendChatUsage", "BackendAgentTools"),
    ):
        statements = [
            st
            for st in _statements(default, runtime)
            if any(a.startswith("lambda:Invoke") for a in _actions([st]))
        ]
        assert {a for st in statements for a in _actions([st])} == {
            "lambda:InvokeFunctionUrl",
            "lambda:InvokeFunction",
        }
        text = json.dumps(statements)
        assert own in text and other not in text
        assert '"lambda:FunctionUrlAuthType": "AWS_IAM"' in text
        assert '"lambda:InvokedViaFunctionUrl": "true"' in text
    assert "execute-api:Invoke" not in json.dumps(default.props("AWS::IAM::Policy"))


def test_runtimes_are_told_their_function_urls(default: Synth) -> None:
    env = {
        json.dumps(r["Properties"]["AgentRuntimeName"]): r["Properties"][
            "EnvironmentVariables"
        ]
        for r in default.resources("AWS::BedrockAgentCore::Runtime").values()
    }
    module = next(v for k, v in env.items() if "modules" in k)
    chat = next(v for k, v in env.items() if "chat" in k)
    assert "BackendAgentTools" in json.dumps(module["AIR_AGENT_TOOLS_URL"])
    assert "BackendChatUsage" in json.dumps(chat["AIR_CHAT_USAGE_URL"])
    assert "AIR_CHAT_USAGE_URL" not in module and "AIR_AGENT_TOOLS_URL" not in chat
    assert chat["AIR_CHAT_MODEL_KEY"] == "sonnet-5-5"


@pytest.mark.parametrize("gate", ["BackendAgentToolsRole", "BackendChatUsageRole"])
def test_agent_gates_cannot_administer_users_sign_tokens_or_start_runs(
    default: Synth, gate: str
) -> None:
    actions = _actions(_statements(default, gate))
    assert actions, gate
    assert not {a for a in actions if a.startswith("cognito-idp:")}
    assert (
        not {"kms:GenerateMac", "states:StartExecution", "lambda:InvokeFunction"}
        & actions
    )


def test_invoke_can_record_a_run_that_never_started(default: Synth) -> None:
    """Invoke finalizes a run CANCELLED itself when the kill switch is on or AgentCore refuses the start
    (workers.invoke), which writes the run and releases its budget hold in the ops table. A read-only
    grant made that write fail silently and left the run open with its hold charged. The grant is only the
    partitions finalize touches: MODULE#* (the run records, and so also the module definitions in the same
    partitions) and BUDGET#*, never the audit rows, the seed, the kill switch or the chat ledgers."""
    statements = _statements(default, "BackendInvokeRole")
    allow = [
        st
        for st in statements
        if st["Effect"] == "Allow" and "dynamodb:" in json.dumps(st.get("Action"))
    ]
    assert allow, "no DynamoDB statement found"
    by_keys = {
        tuple(st["Condition"]["ForAllValues:StringLike"]["dynamodb:LeadingKeys"]): st
        for st in allow
    }
    assert set(by_keys) == {("MODULE#*", "BUDGET#*"), ("ANDON",)}
    writes = {"dynamodb:PutItem", "dynamodb:UpdateItem"}
    assert writes <= _actions([by_keys[("MODULE#*", "BUDGET#*")]])
    assert not (writes | {"dynamodb:DeleteItem"}) & _actions([by_keys[("ANDON",)]])
    assert all("OpsTable" in json.dumps(st["Resource"]) for st in allow)
    # BUDGET#* also matches the chat ledgers: an explicit Deny takes them out.
    deny = [
        st
        for st in statements
        if st["Effect"] == "Deny"
        and "ForAnyValue:StringLike" in st.get("Condition", {})
    ]
    assert [
        st["Condition"]["ForAnyValue:StringLike"]["dynamodb:LeadingKeys"] for st in deny
    ] == [["BUDGET#chat", "BUDGET#chat-*"]]
    assert [
        st
        for st in statements
        if st["Effect"] == "Deny" and st.get("Action") == "dynamodb:Scan"
    ]
    # The table is encrypted with the data key, so the writes need GenerateDataKey on that key alone.
    kms = [st for st in statements if "kms:GenerateDataKey*" in _actions([st])]
    assert kms and all("DataDataKey" in json.dumps(st["Resource"]) for st in kms)


def test_the_tool_gate_reads_radars_and_writes_only_drafts(default: Synth) -> None:
    statements = _statements(default, "BackendAgentToolsRole")
    objects = [
        json.dumps(st["Resource"])
        for st in statements
        if any(a.startswith("s3:") and "Object" in a for a in _actions([st]))
    ]
    joined = " ".join(objects)
    assert "/radars/*" in joined and "/drafts/*" in joined
    assert '"/*"' not in joined and "docs/" not in joined
    writes = [st for st in statements if "s3:PutObject" in _actions([st])]
    assert writes and all("/drafts/*" in json.dumps(st["Resource"]) for st in writes)


# ---- Run bookkeeping ------------------------------------------------------------------------------


def _definition(s: Synth) -> Any:
    (sm,) = s.props("AWS::StepFunctions::StateMachine")
    return json.loads(
        "".join(
            p if isinstance(p, str) else "X"
            for p in sm["DefinitionString"]["Fn::Join"][1]
        )
    )


def test_finalize_retries_every_failure_and_a_failed_success_is_recorded_as_failed(
    default: Synth,
) -> None:
    states = _definition(default)["States"]
    for name in ("FinalizeSucceeded", "FinalizeFailed"):
        (retry,) = [
            r for r in states[name]["Retry"] if "States.TaskFailed" in r["ErrorEquals"]
        ]
        assert "Lambda.Unknown" in retry["ErrorEquals"]
        assert retry["MaxAttempts"] >= 6 and retry["JitterStrategy"] == "FULL"
    (catch,) = states["FinalizeSucceeded"]["Catch"]
    assert catch["Next"] == "FinalizeFailed" and catch["ErrorEquals"] == ["States.ALL"]


def test_cr05_a_throttled_run_invoke_is_retried_not_failed(default: Synth) -> None:
    states = _definition(default)["States"]
    (retry,) = [
        r
        for r in states["RunModule"]["Retry"]
        if r["ErrorEquals"] == ["Lambda.TooManyRequestsException"]
    ]
    assert retry["MaxAttempts"] >= 6 and retry["JitterStrategy"] == "FULL"
    assert retry["BackoffRate"] == 2 and retry["MaxDelaySeconds"] == 60
    # The catch to FinalizeFailed still applies once the retries are used up.
    assert states["RunModule"]["Catch"][-1]["Next"] == "FinalizeFailed"


def test_reserved_concurrency_is_opt_in(default: Synth, bundles: None) -> None:
    def reserved(s: Synth) -> dict[str, int]:
        return {
            k: v["Properties"]["ReservedConcurrentExecutions"]
            for k, v in s.resources("AWS::Lambda::Function").items()
            if "ReservedConcurrentExecutions" in v["Properties"]
        }

    assert reserved(default) == {}
    found = reserved(synth(reservedConcurrency="true"))
    # 2 each for the run bookkeeping, 1 each for the sweep and the source watcher, and 10 each for the two agent gates: those
    # reach neither WAF nor the API throttle, so nothing else bounds them (P2-086).
    assert sorted(found.values()) == [1, 1, 2, 2, 10, 10]
    assert all(
        k.startswith(
            (
                "BackendInvoke",
                "BackendFinalize",
                "BackendSweep",
                "BackendWatch",
                "BackendAgentTools",
                "BackendChatUsage",
            )
        )
        for k in found
    )


# ---- Alarms -------------------------------------------------------------------------------------


def _alarms(s: Synth) -> dict[str, dict[str, Any]]:
    return {a["AlarmDescription"]: a for a in s.props("AWS::CloudWatch::Alarm")}


def _metrics(alarm: dict[str, Any]) -> set[tuple[str, str]]:
    """(namespace, metric name) of an alarm, whichever form CloudFormation needs: a metric with a
    label or another Region renders as a Metrics array rather than flat properties."""
    return {(ns, name) for ns, name, _, _ in _alarm_metrics(alarm)}


INSIGHTS = re.compile(
    r"SELECT \w+\((?P<name>[\w.-]+)\) FROM \"(?P<ns>[^\"]+)\""
    r"(?: WHERE (?P<where>.*))?$"
)


def _alarm_metrics(
    alarm: dict[str, Any],
) -> list[tuple[str, str, dict[str, Any], bool]]:
    """Every metric an alarm reads, as (namespace, metric name, dimensions): flat properties, a
    MetricStat in a Metrics array, or a Metrics Insights query (whose WHERE names the dimensions;
    a math expression over other entries reads nothing itself). The flag is False for a query,
    which sums over every dimension it does not filter on."""
    if "MetricName" in alarm:
        dims = {d["Name"]: d["Value"] for d in alarm.get("Dimensions", [])}
        return [(alarm["Namespace"], alarm["MetricName"], dims, True)]
    out: list[tuple[str, str, dict[str, Any], bool]] = []
    for m in alarm.get("Metrics", []):
        if "MetricStat" in m:
            metric = m["MetricStat"]["Metric"]
            dims = {d["Name"]: d["Value"] for d in metric.get("Dimensions", [])}
            out.append((metric["Namespace"], metric["MetricName"], dims, True))
            continue
        expression = m["Expression"]
        parts = (
            expression["Fn::Join"][1] if isinstance(expression, dict) else [expression]
        )
        text = "".join(p if isinstance(p, str) else "<token>" for p in parts)
        query = INSIGHTS.match(text)
        if query is None:
            continue
        dims = {}
        for clause in (query["where"] or "").split(" AND "):
            if clause:
                name, _ = clause.split(" = ", 1)
                dims[name] = [p for p in parts if not isinstance(p, str)]
        out.append((query["ns"], query["name"], dims, False))
    return out


PUBLISHED = json.loads(
    (Path(__file__).parent / "fixtures" / "published_metrics.json").read_text()
)


def unpublished_alarm_metrics(s: Synth) -> list[str]:
    """Alarms that read a metric no service publishes under that namespace, name and set of
    dimension names (r3 P3-028): such an alarm never gets a data point and, treating missing data
    as not breaching, never fires. AWS metrics are checked against recorded list-metrics output,
    the deployment's own namespace against its metric filters (which publish no dimensions)."""
    filters = {
        (t["MetricNamespace"], t["MetricName"])
        for f in s.props("AWS::Logs::MetricFilter")
        for t in f["MetricTransformations"]
    }
    bad: list[str] = []
    for alarm in _alarms(s).values():
        for ns, name, dims, exact in _alarm_metrics(alarm):
            if ns.startswith("AWS/"):
                published = PUBLISHED.get(ns, {}).get(name, [])
                ok = (
                    sorted(dims) in published
                    if exact
                    else any(set(dims) <= set(p) for p in published)
                )
            else:
                ok = (ns, name) in filters and not dims
            if not ok:
                bad.append(f"{alarm['AlarmDescription']}: {ns} {name} {sorted(dims)}")
    return bad


def _with_metric(s: Synth, namespace: str, name: str) -> list[dict[str, Any]]:
    return [a for a in _alarms(s).values() if (namespace, name) in _metrics(a)]


def test_a_single_failure_of_a_run_or_its_bookkeeping_alarms(default: Synth) -> None:
    alarms = _alarms(default)
    assert alarms["A module run failed in the workflow"]["Threshold"] == 1
    assert alarms["Invoke function errors"]["Threshold"] == 1
    assert alarms["Finalize function errors"]["Threshold"] == 1
    assert alarms["ApiHandler function errors"]["Threshold"] == 3
    assert alarms["Indexer function throttles"]["Threshold"] == 1


def test_timeouts_are_counted_from_the_workflow_log(default: Synth) -> None:
    (flt,) = [
        f
        for f in default.props("AWS::Logs::MetricFilter")
        if f["MetricTransformations"][0]["MetricName"] == "RunTimeouts"
    ]
    assert (
        "TaskTimedOut" in flt["FilterPattern"]
        and "ExecutionTimedOut" in flt["FilterPattern"]
    )
    alarm = next(
        a for d, a in _alarms(default).items() if d.startswith("A module run timed out")
    )
    # P2-020: the namespace carries the prefix, so a test stack's timeouts never page the production
    # on-call (a metric filter cannot set a static dimension, and the metric has none).
    assert (
        alarm["MetricName"] == "RunTimeouts"
        and alarm["Namespace"] == "AgenticInsightsRadar/air"
    )


def test_the_platform_alarms_cover_stream_lag_api_errors_runtimes_and_sessions(
    default: Synth,
) -> None:
    alarms = _alarms(default)
    lag = next(
        a
        for d, a in alarms.items()
        if d.startswith("The indexer is over an hour behind")
    )
    assert (lag["MetricName"], lag["Threshold"]) == ("IteratorAge", 3_600_000)
    api = alarms["The HTTP API returned server errors"]
    assert (api["Namespace"], api["MetricName"]) == ("AWS/ApiGateway", "5xx")
    # P2-025: per runtime, so other agents neither trip nor hide this deployment's load; and the
    # account-wide quota, which every agent in the account shares (r3 P3-005).
    sessions = [a for d, a in alarms.items() if "concurrent sessions" in d]
    assert len(sessions) == 2
    assert all(("AWS/Bedrock-AgentCore", "Sessions") in _metrics(a) for a in sessions)
    assert all(a["Threshold"] == monitoring.RUNTIME_SESSIONS for a in sessions)
    (account,) = _with_metric(default, "AWS/Bedrock-AgentCore", "ActiveSessionCount")
    assert account["Threshold"] == 4000  # 80% of us-east-1's default of 5,000
    runtime = [d for d in alarms if d.startswith("AgentCore ")]
    assert (
        len(runtime) == 10
    )  # errors, throttles, user errors, auth failures, sessions per runtime
    assert (
        len(alarms) == 46
    )  # 9 functions x 2, 8 workflow, queue and rate ceiling, 2 API, 4 function URL, 10 runtime,
    # 1 account sessions, 1 web-search gateway, 2 edge
    assert len(default.resources("AWS::CloudWatch::Dashboard")) == 1
    for a in alarms.values():
        assert a["AlarmActions"], a["AlarmDescription"]


def test_p2006_a_tool_gate_that_refuses_every_call_raises_an_alarm(
    default: Synth,
) -> None:
    """The handler answers every exception as an HTTP response, so the Lambda Errors metric of a tool
    gate never moves: the function URL metrics are the only signal (P2-006)."""
    assert (
        len(_with_metric(default, "AWS/Lambda", "Url5xxCount")) == 2
    )  # AgentTools and ChatUsage
    rates = _with_metric(default, "AWS/Lambda", "Url4xxCount")
    assert len(rates) == 2
    assert all(
        "function URL calls were refused" in a["AlarmDescription"] for a in rates
    )
    assert all(
        ("AWS/Lambda", "UrlRequestCount") in _metrics(a) for a in rates
    )  # a rate, not a count
    api_rate = _alarms(default)[
        f"More than {monitoring.API_4XX_PERCENT}% of HTTP API requests were refused (4xx)"
    ]
    assert _metrics(api_rate) == {
        ("AWS/ApiGateway", "4xx"),
        ("AWS/ApiGateway", "Count"),
    }


def test_p2024_an_authorizer_that_refuses_every_user_raises_an_alarm(
    default: Synth,
) -> None:
    for name in ("UserErrors", "InboundAuthorizationFailure", "Sessions"):
        assert len(_with_metric(default, "AWS/Bedrock-AgentCore", name)) == 2, name


@pytest.mark.parametrize("which", ["default", "other_region", "disposable"])
def test_p3028_every_alarm_reads_a_metric_its_service_publishes(
    which: str, request: pytest.FixtureRequest
) -> None:
    s: Synth = request.getfixturevalue(which)
    assert unpublished_alarm_metrics(s) == []
    assert all(_alarm_metrics(a) for a in _alarms(s).values())


def test_p3028_the_check_refuses_the_alarms_that_could_never_fire() -> None:
    """The three alarm shapes r3 found live (P3-004 to P3-006) fail the check."""
    for ns, name, dims in (
        ("AWS/WAFV2", "BlockedRequests", ["Region", "Rule", "WebACL"]),
        ("AWS/Bedrock-AgentCore", "SessionCount", ["Name", "Operation", "Resource"]),
        (
            "AWS/Bedrock-AgentCore",
            "InboundAuthorizationFailure",
            ["Name", "Operation", "Resource"],
        ),
    ):
        assert dims not in PUBLISHED.get(ns, {}).get(name, []), name


def test_p3004_p3006_the_alarm_dimensions_name_this_deployments_resources(
    default: Synth,
) -> None:
    """The values, not just the names: the WAF alarm reads the ACL's own (generated) name, taken
    from its ARN; the auth-failure query and the session alarm name this runtime's ARN."""
    ((acl, _),) = default.resources("AWS::WAFv2::WebACL").items()
    (waf,) = _with_metric(default, "AWS/WAFV2", "BlockedRequests")
    ((_, _, dims, exact),) = _alarm_metrics(waf)
    assert exact
    assert dims == {
        "Rule": "ALL",
        "WebACL": {
            "Fn::Select": [2, {"Fn::Split": ["/", {"Fn::GetAtt": [acl, "Arn"]}]}]
        },
    }
    runtimes = {
        k: v["Properties"]["AgentRuntimeName"]
        for k, v in default.resources("AWS::BedrockAgentCore::Runtime").items()
    }
    for runtime_id, runtime_name in runtimes.items():
        arn = {"Fn::GetAtt": [runtime_id, "AgentRuntimeArn"]}
        (auth,) = [
            a
            for a in _with_metric(
                default, "AWS/Bedrock-AgentCore", "InboundAuthorizationFailure"
            )
            if arn in json.loads(json.dumps(_alarm_metrics(a)[0][2]["ResourceId"]))
        ]
        assert auth["Threshold"] == 5
        (sessions,) = [
            a
            for a in _with_metric(default, "AWS/Bedrock-AgentCore", "Sessions")
            if a["Dimensions"][2]["Value"] == arn
        ]
        assert sessions["Dimensions"][0]["Value"] == f"{runtime_name}::DEFAULT"


def test_p3005_the_account_session_alarm_follows_the_regions_default_quota(
    other_region: Synth,
) -> None:
    (account,) = _with_metric(
        other_region, "AWS/Bedrock-AgentCore", "ActiveSessionCount"
    )
    assert account["Threshold"] == 2000  # 80% of 2,500 outside us-east-1 and us-west-2
    assert "eu-west-1" in account["AlarmDescription"]


def test_p2026_the_edge_alarms_and_widgets_read_the_us_east_1_metrics(
    default: Synth, other_region: Synth
) -> None:
    assert len(_with_metric(default, "AWS/WAFV2", "BlockedRequests")) == 1
    assert len(_with_metric(default, "AWS/CloudFront", "5xxErrorRate")) == 1
    # Outside us-east-1 a CloudWatch alarm cannot read those metrics at all, so only the dashboard
    # widget (which can) is left.
    assert not _with_metric(other_region, "AWS/WAFV2", "BlockedRequests")
    assert not _with_metric(other_region, "AWS/CloudFront", "5xxErrorRate")
    (board,) = other_region.props("AWS::CloudWatch::Dashboard")
    body = json.dumps(board["DashboardBody"])
    assert "Edge (us-east-1)" in body and "BlockedRequests" in body


def test_p2008_every_fixed_global_or_regional_name_carries_the_region(
    default: Synth,
) -> None:
    (board,) = default.props("AWS::CloudWatch::Dashboard")
    assert board["DashboardName"] == "air-insights-radar-us-east-1"
    names = [board["DashboardName"]]
    names += [
        b["BucketName"]
        for b in default.props("AWS::S3::Bucket")
        if isinstance(b.get("BucketName"), str)
    ]
    names += [
        g["LogGroupName"]
        for g in default.props("AWS::Logs::LogGroup")
        if isinstance(g.get("LogGroupName"), str)
    ]
    assert names, "no fixed names found: the test would pass for the wrong reason"
    for name in names:
        assert "us-east-1" in name, name


# ---- Edge ---------------------------------------------------------------------------------------


def test_waf_logs_every_request_with_credentials_redacted(default: Synth) -> None:
    (cfg,) = default.props("AWS::WAFv2::LoggingConfiguration")
    redacted = {
        f["SingleHeader"]["Name"] for f in cfg["RedactedFields"] if "SingleHeader" in f
    }
    assert redacted == {"authorization", "cookie", "x-mr-run-token"}
    # P2-036: and the query string, which carries every sign-in's OAuth code and state
    # (/auth/callback) and users' search terms (?q=), for logRetentionDays.
    assert {"QueryString": {}} in cfg["RedactedFields"]
    destination = json.dumps(cfg["LogDestinationConfigs"])
    assert (
        "log-group:aws-waf-logs-air-us-east-1" in destination
        and not destination.endswith(':*"]')
    )
    groups = [
        g
        for g in default.props("AWS::Logs::LogGroup")
        if g.get("LogGroupName") == "aws-waf-logs-air-us-east-1"
    ]
    assert len(groups) == 1 and groups[0]["KmsKeyId"]


def test_p2037_the_access_log_ties_a_call_to_a_verified_user(
    default: Synth,
) -> None:
    (stage,) = default.props("AWS::ApiGatewayV2::Stage")
    fmt = stage["AccessLogSettings"]["Format"]
    assert "$context.authorizer.claims.sub" in fmt  # verified, not claimed
    # HTTP APIs refuse this variable at deploy ("context variables are not supported").
    assert "xForwardedFor" not in fmt
    assert (
        "$context.identity.sourceIp" in fmt
    )  # the CloudFront edge, kept for comparison


def test_the_edge_stack_logs_its_web_acl_with_its_own_key(bundles: None) -> None:
    (edge,) = build("eu-west-1")
    t = Template.from_stack(edge)
    t.resource_count_is("AWS::WAFv2::LoggingConfiguration", 1)
    t.has_resource_properties(
        "AWS::Logs::LogGroup", {"LogGroupName": "aws-waf-logs-air-eu-west-1"}
    )
    t.has_resource_properties("AWS::KMS::Key", {"EnableKeyRotation": True})


def test_csp_loads_no_remote_images(default: Synth) -> None:
    (
        policy,
    ) = [  # the SPA's policy: the one that sets its CSP over the origin's (scan cycle 42)
        p
        for p in default.props("AWS::CloudFront::ResponseHeadersPolicy")
        if p["ResponseHeadersPolicyConfig"]["SecurityHeadersConfig"][
            "ContentSecurityPolicy"
        ]["Override"]
    ]
    value = json.dumps(
        policy["ResponseHeadersPolicyConfig"]["SecurityHeadersConfig"][
            "ContentSecurityPolicy"
        ]
    )
    assert "img-src 'self' data: blob:;" in value and "https:;" not in value


def test_site_upgrades_keep_old_files_and_never_cache_index_html(
    default: Synth,
) -> None:
    deployments = default.resources("Custom::CDKBucketDeployment")
    assert len(deployments) == 2
    for d in deployments.values():
        assert d["Properties"]["Prune"] is False
    index = next(d for k, d in deployments.items() if k.startswith("WebDeployIndex"))
    assert index["Properties"]["SystemMetadata"]["cache-control"] == "no-cache"
    assert index["Properties"]["DistributionPaths"] == ["/index.html", "/"]
    (site,) = [
        b
        for k, b in default.resources("AWS::S3::Bucket").items()
        if k.startswith("WebSite")
    ]
    assert site["Properties"]["VersioningConfiguration"] == {"Status": "Enabled"}


@pytest.mark.parametrize(
    ("value", "expected"), [("100", "PriceClass_100"), ("All", "PriceClass_All")]
)
def test_price_class_is_an_option(value: str, expected: str, bundles: None) -> None:
    s = synth(priceClass=value)
    (d,) = s.props("AWS::CloudFront::Distribution")
    assert d["DistributionConfig"]["PriceClass"] == expected


# ---- Cost, backup ---------------------------------------------------------------------------------


def test_no_budget_unless_asked_for(default: Synth, bundles: None) -> None:
    assert default.resources("AWS::Budgets::Budget") == {}
    s = synth(monthlyBudgetUsd="500")
    budget, account = s.props("AWS::Budgets::Budget")
    assert (
        budget["Budget"]["BudgetLimit"]
        == account["Budget"]["BudgetLimit"]
        == {"Amount": 500, "Unit": "USD"}
    )
    kinds = {
        n["Notification"]["NotificationType"]
        for n in budget["NotificationsWithSubscribers"]
    }
    assert kinds == {"ACTUAL", "FORECASTED"}
    assert all(
        n["Subscribers"][0]["SubscriptionType"] == "SNS"
        for n in budget["NotificationsWithSubscribers"]
    )
    assert "budgets.amazonaws.com" in json.dumps(s.props("AWS::SNS::TopicPolicy"))
    assert [
        e.entry.data
        for e in s.annotations.find_error(
            "*", Match.string_like_regexp("AwsSolutions-.*")
        )
    ] == []


def test_backup_plan_is_opt_in_and_covers_tables_and_documents(
    default: Synth, bundles: None
) -> None:
    assert default.resources("AWS::Backup::BackupPlan") == {}
    s = synth(backupPlan="true")
    (selection,) = s.props("AWS::Backup::BackupSelection")
    resources = json.dumps(selection["BackupSelection"]["Resources"])
    assert (
        resources.count("DataGraphTable") == 1
        and "DataOpsTable" in resources
        and "DataAssets" in resources
    )
    (plan,) = s.props("AWS::Backup::BackupPlan")
    assert (
        plan["BackupPlan"]["BackupPlanRule"][0]["Lifecycle"]["DeleteAfterDays"] == 120
    )
    (vault,) = s.props("AWS::Backup::BackupVault")
    assert vault["EncryptionKeyArn"]
    errors = s.annotations.find_error("*", Match.string_like_regexp("AwsSolutions-.*"))
    warnings = s.annotations.find_warning(
        "*", Match.string_like_regexp("AwsSolutions-.*")
    )
    assert [e.entry.data for e in errors + warnings] == []


@pytest.mark.parametrize("retain", ["true", "false"])
def test_p3_011_the_backup_vault_is_kept_whatever_retain_data_says(
    bundles: None, retain: str
) -> None:
    """A vault with recovery points cannot be deleted and nothing in the stack deletes them, so a
    DESTROY vault ended every retainData=false destroy at DELETE_FAILED (P3-011). DeletionPolicy is not a
    resource property: CloudFormation applies the change without updating the vault, under the same
    logical id."""
    s = synth(backupPlan="true", retainData=retain)
    (lid,) = s.resources("AWS::Backup::BackupVault")
    assert lid.startswith("BackupsVault")
    vault = s.resources("AWS::Backup::BackupVault")[lid]
    assert (
        vault["DeletionPolicy"] == "Retain" and vault["UpdateReplacePolicy"] == "Retain"
    )


# ---- Sign-in ------------------------------------------------------------------------------------


def test_app_clients_allow_only_the_hosted_ui_and_refresh(
    default: Synth, bundles: None
) -> None:
    for c in default.props("AWS::Cognito::UserPoolClient"):
        assert c["ExplicitAuthFlows"] == ["ALLOW_REFRESH_TOKEN_AUTH"]
    # A stack the e2e suite runs against lets test users sign in with SRP on the MCP client only.
    flows = sorted(
        c["ExplicitAuthFlows"]
        for c in synth(e2eSignIn="true").props("AWS::Cognito::UserPoolClient")
    )
    assert flows == [
        ["ALLOW_REFRESH_TOKEN_AUTH"],
        ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    ]


def test_validation_stacks_can_suppress_the_invitation(
    default: Synth, bundles: None
) -> None:
    (user,) = default.props("AWS::Cognito::UserPoolUser")
    assert "MessageAction" not in user
    (user,) = synth(suppressInvitation="true").props("AWS::Cognito::UserPoolUser")
    assert user["MessageAction"] == "SUPPRESS"


# ---- Agents: models, guardrail, sessions --------------------------------------------------------


def _catalog_profiles() -> list[str]:
    text = (ROOT / "backend/src/insights_radar/modules/catalog.py").read_text()
    return re.findall(r'"(global\.[a-z0-9.:-]+)"', text)


def _catalog_keys() -> list[str]:
    text = (ROOT / "backend/src/insights_radar/modules/catalog.py").read_text()
    return re.findall(r'ModelSpec\(\s*"([a-z0-9-]+)"', text)


def test_model_scope_is_exactly_the_catalog(default: Synth) -> None:
    assert list(agents.MODEL_PROFILES) == _catalog_profiles()
    assert list(MODEL_KEYS) == _catalog_keys()
    resources = [
        json.dumps(r)
        for st in _statements(default, "AgentsModuleRuntime")
        if "bedrock:InvokeModel" in _actions([st])
        for r in st["Resource"]
    ]
    assert len(resources) == 3 * len(agents.MODEL_PROFILES)
    assert not [
        r for r in resources if r.rstrip('"]}').endswith("*")
    ]  # no model-family wildcard


def test_guardrail_version_follows_its_filters(
    default: Synth, monkeypatch: pytest.MonkeyPatch
) -> None:
    (version,) = default.props("AWS::Bedrock::GuardrailVersion")
    assert agents.guardrail_revision() in version["Description"]
    before = agents.guardrail_revision()
    monkeypatch.setattr(agents, "CONTENT_FILTERS", (("PROMPT_ATTACK", "HIGH", "NONE"),))
    assert agents.guardrail_revision() != before
    (guardrail,) = default.props("AWS::Bedrock::Guardrail")
    pii = guardrail["SensitiveInformationPolicyConfig"]["PiiEntitiesConfig"]
    assert {p["Type"] for p in pii} == {
        "AWS_ACCESS_KEY",
        "AWS_SECRET_KEY",
        "CREDIT_DEBIT_CARD_NUMBER",
    }
    assert all(
        p["InputEnabled"] is False and p["OutputAction"] == "ANONYMIZE" for p in pii
    )


def test_chat_sessions_end_after_five_idle_minutes(default: Synth) -> None:
    lifecycle = {
        json.dumps(r["AgentRuntimeName"]): r["LifecycleConfiguration"][
            "IdleRuntimeSessionTimeout"
        ]
        for r in default.props("AWS::BedrockAgentCore::Runtime")
    }
    assert sorted(lifecycle.values()) == [300, 900]


def test_templates_name_no_fixed_partition(default: Synth) -> None:
    assert '"arn:aws:' not in json.dumps(default.template.to_json())


# ---- Options ----------------------------------------------------------------------------------


def test_the_stack_records_its_options_for_updates(disposable: Synth) -> None:
    out = disposable.template.to_json()["Outputs"]["DeployOptions"]["Value"]
    assert (
        json.loads(out)
        == {
            "retainData": "false",
            "webSearch": "false",
            "logRetentionDays": "30",
            "agentLogRetentionDays": "30",
            "chatMemoryDays": "30",
            "chatMemoryKey": "data-key",
            "chatModel": "sonnet-5-5",
            "priceClass": "100",
            "wafRateLimit": "2000",
            "guardrailGeneration": "1",
            "slackSecretGeneration": "0",
            "unsubscribeKeyGeneration": "0",
            "workspaceRatePerMinute": "600",
            "monthlyBudgetUsd": "0",
            "reservedConcurrency": "false",
            "backupPlan": "false",
            "suppressInvitation": "false",
            # P2-017: recorded, never kept. deploy.sh asks for it on every deploy and reads this value
            # only to say that it is turning SRP sign-in off again.
            "e2eSignIn": "false",
            "solutionVersion": "1.1.0",  # P3-051: which release, read by operators, never kept
            "alarmEmail": "ops@example.com",
        }
    )


def test_config_reads_the_new_options() -> None:
    c = DeployConfig.from_context(
        {
            "adminEmail": "a@example.com",
            "agentLogRetentionDays": "90",
            "chatModel": "haiku-4-5",
            "priceClass": "200",
            "monthlyBudgetUsd": "250",
            "reservedConcurrency": "TRUE",
            "backupPlan": "true",
            "suppressInvitation": "true",
            "chatMemoryDays": "7",
        }.get
    )
    assert (
        c.agent_log_retention_days,
        c.chat_model,
        c.price_class,
        c.monthly_budget_usd,
        c.chat_memory_days,
    ) == (90, "haiku-4-5", "200", 250, 7)
    assert c.reserved_concurrency and c.backup_plan and c.suppress_invitation
    assert c.alarm_address == "a@example.com"


@pytest.mark.parametrize(
    "key",
    [
        "retainData",
        "webSearch",
        "reservedConcurrency",
        "backupPlan",
        "suppressInvitation",
        "e2eSignIn",
        "deletionProtection",
    ],
)
@pytest.mark.parametrize("value", ["0", "1", "no", "yes", "on", "off"])
def test_p2050_a_boolean_option_is_only_true_or_false(key: str, value: str) -> None:
    """0, no and yes used to mean different things for different options: AIR_WEB_SEARCH=0 left web
    search on while AIR_BACKUP=0 turned backups off, and AIR_E2E_SIGN_IN=1 opened SRP sign-in with
    no warning. Anything but true or false now stops the deploy."""
    with pytest.raises(ValueError, match=f"{key} must be true or false"):
        DeployConfig.from_context({"adminEmail": "a@example.com", key: value}.get)


def test_p2089_chat_memory_retention_is_an_option_within_the_services_limits() -> None:
    assert DeployConfig(admin_email="a@example.com").chat_memory_days == 30
    for value in ("6", "366", "thirty"):
        with pytest.raises(ValueError, match="chatMemoryDays"):
            DeployConfig.from_context(
                {"adminEmail": "a@example.com", "chatMemoryDays": value}.get
            )


def test_cr201_an_existing_deployment_keeps_its_chat_memory_unchanged() -> None:
    """EncryptionKeyArn and Name are both create-only. A deployment created before the data key was
    used for chat history keeps exactly the memory it has (the r1 properties: same name, no key),
    because CloudFormation cannot replace a custom-named resource under the same name and the whole
    update rolled back. Only an opt-in data key gets a new name, so that change is a real
    create-then-delete replacement."""
    kept = synth(chatMemoryKey="aws-owned")
    (memory,) = kept.props("AWS::BedrockAgentCore::Memory")
    assert memory["Name"] == "air_chat"
    assert "EncryptionKeyArn" not in memory
    (fresh,) = synth().props("AWS::BedrockAgentCore::Memory")
    assert fresh["Name"] == "air_chat_k" and "EncryptionKeyArn" in fresh
    assert DeployConfig(admin_email="a@example.com").chat_memory_key == "data-key"
    with pytest.raises(ValueError, match="chatMemoryKey must be one of"):
        DeployConfig.from_context(
            {"adminEmail": "a@example.com", "chatMemoryKey": "cmk"}.get
        )


def test_p2019_personal_data_and_the_guardrail_use_the_data_key(default: Synth) -> None:
    key = next(
        k for k in default.resources("AWS::KMS::Key") if k.startswith("DataDataKey")
    )
    (memory,) = default.props("AWS::BedrockAgentCore::Memory")
    (guardrail,) = default.props("AWS::Bedrock::Guardrail")
    (gateway,) = default.props("AWS::BedrockAgentCore::Gateway")
    for prop in (
        memory["EncryptionKeyArn"],
        guardrail["KmsKeyArn"],
        gateway["KmsKeyArn"],
    ):
        assert prop == {"Fn::GetAtt": [key, "Arn"]}, prop
    # Applying a guardrail encrypted with a customer managed key needs the key.
    for role in ("AgentsModuleRuntime", "AgentsChatRuntime"):
        actions = [
            st["Action"]
            for st in _statements(default, role)
            if "kms:Decrypt" in json.dumps(st.get("Action"))
        ]
        assert actions, role


# Logical IDs of the r1 (c408510) template that is deployed today. The upgrade must keep the first
# two, so CloudFormation updates them; it must not keep the last two, so it replaces them.
R1_MEMORY = "AgentsChatMemory769E3C49"
R1_GUARDRAIL = "AgentsGuardrail44AD901A"
R1_STATE_MACHINE = "BackendRunsF5412AD5"
R1_GATEWAY = "AgentsWebSearchGatewayF72588EE"
R1_GATEWAY_TARGET = "AgentsWebSearchTargetA9F14948"


def _data_key(s: Synth) -> dict[str, Any]:
    key = next(k for k in s.resources("AWS::KMS::Key") if k.startswith("DataDataKey"))
    return {"Fn::GetAtt": [key, "Arn"]}


@pytest.mark.parametrize("memory_key", ["aws-owned", "data-key"])
def test_upgrade_no_resource_has_its_create_only_key_changed_in_place(
    bundles: None, memory_key: str
) -> None:
    """The r2 upgrade of a deployed r1 stack rolled back at the web-search gateway: "KMS key cannot
    be updated for an existing gateway". Per resource P2-019 touched:

    - Memory: ``EncryptionKeyArn`` is create-only. ``aws-owned`` (every r1 deployment, read back by
      deploy.sh) keeps the r1 resource as it is: same logical ID, same name, no key.
    - Guardrail: ``KmsKeyArn`` updates in place, so the r1 resource keeps its logical ID and gets
      the data key, after the runtimes may decrypt with it.
    - Gateway and its target: the key cannot change in place, so both get new logical IDs and the
      gateway a new name; the module runtime is pointed at the new URL in the same template.
    - State machine: left on the AWS-owned key, as in r1 (no ``EncryptionConfiguration``).
    """
    s = synth(chatMemoryKey=memory_key)
    key = _data_key(s)
    memories = s.resources("AWS::BedrockAgentCore::Memory")
    if memory_key == "aws-owned":
        assert list(memories) == [R1_MEMORY]
        memory = memories[R1_MEMORY]["Properties"]
        assert memory["Name"] == "air_chat" and "EncryptionKeyArn" not in memory
    else:
        ((_, fresh),) = memories.items()
        assert fresh["Properties"]["Name"] == "air_chat_k"
        assert fresh["Properties"]["EncryptionKeyArn"] == key

    guardrails = s.resources("AWS::Bedrock::Guardrail")
    assert list(guardrails) == [R1_GUARDRAIL]
    guardrail = guardrails[R1_GUARDRAIL]
    assert guardrail["Properties"]["KmsKeyArn"] == key
    (access,) = [
        k
        for k in s.resources("AWS::IAM::Policy")
        if k.startswith("AgentsGuardrailKeyAccess")
    ]
    assert access in guardrail["DependsOn"]
    policy = s.resources("AWS::IAM::Policy")[access]["Properties"]
    assert policy["PolicyDocument"]["Statement"] == [
        {
            "Action": ["kms:Decrypt", "kms:DescribeKey"],
            "Effect": "Allow",
            "Resource": key,
        }
    ]
    assert len(policy["Roles"]) == 2

    gateways = s.resources("AWS::BedrockAgentCore::Gateway")
    ((gateway_id, gateway),) = gateways.items()
    assert gateway_id != R1_GATEWAY and gateway_id.startswith("AgentsWebSearchGatewayK")
    # The name carries the stack id's last uuid group, so an orphan left by a rolled-back
    # attempt cannot block the retry (a fixed "air-web-k" did).
    assert gateway["Properties"]["Name"]["Fn::Join"][1][0] == "air-web"
    assert gateway["Properties"]["KmsKeyArn"] == key
    ((target_id, target),) = s.resources("AWS::BedrockAgentCore::GatewayTarget").items()
    assert target_id != R1_GATEWAY_TARGET
    assert target["Properties"]["GatewayIdentifier"] == {
        "Fn::GetAtt": [gateway_id, "GatewayIdentifier"]
    }
    (module,) = [
        r["Properties"]
        for k, r in s.resources("AWS::BedrockAgentCore::Runtime").items()
        if k.startswith("AgentsModuleRuntime")
    ]
    assert module["EnvironmentVariables"]["AIR_GATEWAY_URL"] == {
        "Fn::GetAtt": [gateway_id, "GatewayUrl"]
    }

    machines = s.resources("AWS::StepFunctions::StateMachine")
    assert list(machines) == [R1_STATE_MACHINE]
    assert "EncryptionConfiguration" not in machines[R1_STATE_MACHINE]["Properties"]
    assert not s.resources("AWS::StepFunctions::Activity")


def _refs(obj: Any, resources: dict[str, Any], under_role: bool = False) -> set[str]:
    """Logical IDs ``obj`` refers to (Ref or GetAtt). With ``under_role``, only those under a
    property whose name says it is a role (RoleArn, MemoryExecutionRoleArn, Role, Roles...), so an
    environment variable that merely carries a role ARN is not taken for the resource's own role."""
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "Ref" and isinstance(v, str) and not under_role:
                out.add(v)
            elif k == "Fn::GetAtt" and not under_role:
                out.add(v[0])
            elif under_role and "Role" in k:
                out |= _refs(v, resources)
            else:
                out |= _refs(v, resources, under_role)
    elif isinstance(obj, list):
        for v in obj:
            out |= _refs(v, resources, under_role)
    return out & set(resources)


def _waits_for(resources: dict[str, Any], lid: str) -> set[str]:
    """Everything ``lid`` is created after and deleted before: DependsOn and references, transitively."""
    out: set[str] = set()
    todo = [lid]
    while todo:
        r = resources[todo.pop()]
        dep = r.get("DependsOn", [])
        for nxt in _refs(r.get("Properties", {}), resources) | set(
            [dep] if isinstance(dep, str) else dep
        ):
            if nxt not in out:
                out.add(nxt)
                todo.append(nxt)
    return out


def key_order_violations(template: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(resource, policy) pairs where a resource on the data key uses a service role whose access
    to the key is in a policy the resource does not wait for (r3 P3-001).

    The resource refers to the role, so CloudFormation deletes the resource before the role, but a
    policy attached to the role is a separate resource that only refers to the role: without a
    dependency it is deleted first, and a service that needs the key to delete (AgentCore does for
    a gateway on a customer managed key) then fails, which is how three upgrades of the live stack
    ended. The same gap on create lets the resource be created before its grant exists.

    The role is the one the resource names in a role property, or the one a resource it refers to
    names (a knowledge base data source uses the knowledge base's role)."""
    resources: dict[str, Any] = template["Resources"]
    (key,) = [
        k
        for k, v in resources.items()
        if v["Type"] == "AWS::KMS::Key" and k.startswith("DataDataKey")
    ]
    policies = {
        k: v["Properties"]
        for k, v in resources.items()
        if v["Type"] == "AWS::IAM::Policy"
    }
    out: list[tuple[str, str]] = []
    for lid, r in resources.items():
        props = r.get("Properties", {})
        if r["Type"].startswith("AWS::IAM::") or key not in _refs(props, resources):
            continue
        roles = _refs(props, resources, under_role=True)
        for hop in _refs(props, resources) - {key}:
            if not resources[hop]["Type"].startswith("AWS::IAM::"):
                roles |= _refs(
                    resources[hop].get("Properties", {}), resources, under_role=True
                )
        roles = {x for x in roles if resources[x]["Type"] == "AWS::IAM::Role"}
        waits = _waits_for(resources, lid)
        for name, policy in policies.items():
            # Every AwsCustomResource has a policy of its own on the one shared provider role, and
            # needs only that one (CDK makes it wait for it): the other calls' policies are not its.
            if r["Type"] == "Custom::AWS" and not name.startswith(
                f"{lid[:-8]}CustomResourcePolicy"
            ):
                continue
            if (
                roles & _refs(policy["Roles"], resources)
                and key in _refs(policy["PolicyDocument"], resources)
                and name not in waits
            ):
                out.append((lid, name))
    return out


@pytest.mark.parametrize(
    "context",
    [
        {},
        {"chatMemoryKey": "aws-owned"},
        {"webSearch": "false"},
        {"retainData": "false", "backupPlan": "true"},
    ],
)
def test_p3001_every_resource_on_the_data_key_waits_for_its_roles_key_grant(
    bundles: None, context: dict[str, str]
) -> None:
    template = synth(**context).template.to_json()
    assert key_order_violations(template) == []


@pytest.mark.parametrize(
    "context",
    [{}, {"webSearch": "false"}, {"backupPlan": "true", "monthlyBudgetUsd": "300"}],
)
def test_p3003_the_seed_runs_after_every_other_resource(
    bundles: None, context: dict[str, str]
) -> None:
    """The Seed's data migrations cannot be rolled back, so it runs last: a failure in any other
    resource happens before it, and the rollback has nothing of it to undo (r3 P3-003)."""
    s = synth(**context)
    resources = s.template.to_json()["Resources"]
    ((seed, _),) = s.resources("Custom::Trigger").items()
    assert set(resources) - _waits_for(resources, seed) == {seed}


def test_p3003_the_seed_runs_last_outside_us_east_1_too(other_region: Synth) -> None:
    resources = other_region.template.to_json()["Resources"]
    ((seed, _),) = other_region.resources("Custom::Trigger").items()
    assert set(resources) - _waits_for(resources, seed) == {seed}


def test_p3001_the_gateway_is_deleted_before_its_key_access(default: Synth) -> None:
    """Delete order is the reverse of DependsOn: the target, then the gateway, then the role's
    policies and the role. The runtimes reach the gateway only through its URL, so they are
    updated to a new gateway before the old one goes."""
    resources = default.template.to_json()["Resources"]
    ((gateway, _),) = default.resources("AWS::BedrockAgentCore::Gateway").items()
    ((target, _),) = default.resources("AWS::BedrockAgentCore::GatewayTarget").items()
    gateway_policies = {
        k
        for k, p in default.resources("AWS::IAM::Policy").items()
        if "AgentsWebSearchGatewayRole" in json.dumps(p["Properties"]["Roles"])
    }
    assert len(gateway_policies) == 2  # ours and the L2's default policy
    for policy in gateway_policies:
        assert policy in _waits_for(resources, gateway)
        assert policy in _waits_for(resources, target)
    (access,) = [
        k for k in gateway_policies if k.startswith("AgentsWebSearchGatewayAccess")
    ]
    actions = _actions(resources[access]["Properties"]["PolicyDocument"]["Statement"])
    assert {
        "kms:Decrypt",
        "kms:GenerateDataKey*",
        "bedrock-agentcore:InvokeWebSearch",
    } <= actions
    assert gateway in _waits_for(resources, target)
    (module,) = [
        k
        for k in default.resources("AWS::BedrockAgentCore::Runtime")
        if k.startswith("AgentsModuleRuntime")
    ]
    assert gateway in _waits_for(resources, module)


def test_p3001_the_audit_catches_a_resource_that_does_not_wait_for_its_grant() -> None:
    """The check itself, on the shape of the deployed template that failed."""
    template = {
        "Resources": {
            "DataDataKey1": {"Type": "AWS::KMS::Key", "Properties": {}},
            "Role": {"Type": "AWS::IAM::Role", "Properties": {}},
            "RoleDefaultPolicy": {
                "Type": "AWS::IAM::Policy",
                "Properties": {
                    "Roles": [{"Ref": "Role"}],
                    "PolicyDocument": {
                        "Statement": [
                            {"Resource": {"Fn::GetAtt": ["DataDataKey1", "Arn"]}}
                        ]
                    },
                },
            },
            "Gateway": {
                "Type": "AWS::BedrockAgentCore::Gateway",
                "Properties": {
                    "RoleArn": {"Fn::GetAtt": ["Role", "Arn"]},
                    "KmsKeyArn": {"Fn::GetAtt": ["DataDataKey1", "Arn"]},
                },
            },
            "Source": {
                "Type": "AWS::Bedrock::DataSource",
                "Properties": {
                    "KnowledgeBaseId": {"Fn::GetAtt": ["Kb", "KnowledgeBaseId"]},
                    "KmsKeyArn": {"Fn::GetAtt": ["DataDataKey1", "Arn"]},
                },
            },
            "Kb": {
                "Type": "AWS::Bedrock::KnowledgeBase",
                "Properties": {"RoleArn": {"Fn::GetAtt": ["Role", "Arn"]}},
            },
            "Fn": {
                "Type": "AWS::Lambda::Function",
                "Properties": {
                    "Environment": {
                        "Variables": {"OTHER_ROLE_ARN": {"Fn::GetAtt": ["Role", "Arn"]}}
                    },
                    "KmsKeyArn": {"Fn::GetAtt": ["DataDataKey1", "Arn"]},
                },
                "DependsOn": [],
            },
        }
    }
    assert sorted(key_order_violations(template)) == [
        ("Gateway", "RoleDefaultPolicy"),
        ("Source", "RoleDefaultPolicy"),
    ]
    template["Resources"]["Gateway"]["DependsOn"] = ["RoleDefaultPolicy"]
    template["Resources"]["Kb"]["DependsOn"] = "RoleDefaultPolicy"
    assert key_order_violations(template) == []


def test_the_gateway_role_trusts_the_replacement_gateways_name(default: Synth) -> None:
    """The role's trust is conditioned on ``gateway/<prefix>-web*``, which the old gateway and
    ``<prefix>-web-k`` both match, so the role serves both while the update runs."""
    (gateway,) = default.props("AWS::BedrockAgentCore::Gateway")
    assert gateway["Name"]["Fn::Join"][1][0] == "air-web"
    assert gateway["RoleArn"]["Fn::GetAtt"][0].startswith("AgentsWebSearchGatewayRole")


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("agentLogRetentionDays", "45", "agentLogRetentionDays"),
        ("chatModel", "gpt", "chatModel"),
        ("priceClass", "300", "priceClass"),
        ("monthlyBudgetUsd", "-5", "monthlyBudgetUsd"),
        ("monthlyBudgetUsd", "2000000", "monthlyBudgetUsd"),
    ],
)
def test_config_rejects_bad_new_options(key: str, value: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        DeployConfig.from_context({"adminEmail": "a@example.com", key: value}.get)


def test_p2011_the_stack_outputs_the_sign_in_domain_to_pin(default: Synth) -> None:
    outputs = default.template.to_json()["Outputs"]
    assert "CognitoDomain" in outputs
    assert "AIR_COGNITO_DOMAIN" in outputs["CognitoDomain"]["Description"]


def test_p2049_retained_resources_name_their_deployment(
    default: Synth, disposable: Synth
) -> None:
    """Two deployments in one account leave keys, buckets and log groups behind; a tag and an alias
    are how an operator tells them apart (P2-049) and how the budget is filtered (P2-048)."""
    for stack, prefix in ((default, "air"), (disposable, "mr-test")):
        aliases = {a["AliasName"] for a in stack.props("AWS::KMS::Alias")}
        assert f"alias/{prefix}-data" in aliases
        assert f"alias/{prefix}-run-token" in aliases
        tagged = [
            r
            for r in stack.props("AWS::S3::Bucket") + stack.props("AWS::KMS::Key")
            if isinstance(r.get("Tags"), list)
        ]
        assert tagged
        for resource in tagged:
            tags = {t["Key"]: t["Value"] for t in resource["Tags"]}
            assert tags["deployment"] == prefix, resource
            assert tags["stack"].startswith("AgenticInsightsRadar"), resource


def test_p2048_the_optional_budget_is_this_deployments_spend_only(
    bundles: None,
) -> None:
    only = synth(monthlyBudgetUsd="500")
    budgets = only.resources("AWS::Budgets::Budget")
    # P3-009 changes no property of the tag budget: same logical id, name and filter, updated in place.
    tagged = next(
        r for lid, r in budgets.items() if lid.startswith("MonitoringMonthlyBudget")
    )["Properties"]
    assert tagged["Budget"]["BudgetName"] == "air-insights-radar-us-east-1"
    assert tagged["Budget"]["CostFilters"] == {"TagKeyValue": ["user:deployment$air"]}


def test_p3_009_an_account_budget_sees_the_untagged_model_spend(bundles: None) -> None:
    """Invocations through the system global. inference profiles carry no resource tag, so the tag budget
    never counts the model spend, about four fifths of the bill (P3-009)."""
    only = synth(monthlyBudgetUsd="500")
    budgets = only.resources("AWS::Budgets::Budget")
    assert sorted(lid[: lid.index("Budget") + 6] for lid in budgets) == [
        "MonitoringAccountBudget",
        "MonitoringMonthlyBudget",
    ]
    account = next(
        r for lid, r in budgets.items() if lid.startswith("MonitoringAccountBudget")
    )["Properties"]
    assert account["Budget"]["BudgetName"] == "air-insights-radar-us-east-1-account"
    assert "CostFilters" not in account["Budget"]
    assert {
        n["Notification"]["NotificationType"]
        for n in account["NotificationsWithSubscribers"]
    } == {
        "ACTUAL",
        "FORECASTED",
    }
    topic = json.dumps(only.props("AWS::SNS::TopicPolicy"))
    assert "budget/air-insights-radar-us-east-1-account" in topic


def _star_statements(s: Synth) -> list[tuple[str, dict[str, Any]]]:
    """Every policy statement in the template whose resource is ``*``, with its policy's logical id."""
    out: list[tuple[str, dict[str, Any]]] = []
    for kind in ("AWS::IAM::Policy", "AWS::IAM::Role", "AWS::IAM::ManagedPolicy"):
        for lid, r in s.resources(kind).items():
            props = r.get("Properties", {})
            documents = [props["PolicyDocument"]] if "PolicyDocument" in props else []
            documents += [p["PolicyDocument"] for p in props.get("Policies", [])]
            for document in documents:
                for st in document["Statement"]:
                    if (
                        st.get("Resource") in ("*", ["*"])
                        and st.get("Effect") != "Deny"
                    ):
                        out.append((lid, st))
    return out


def test_p2010_only_actions_without_resource_level_permissions_use_a_star_resource(
    default: Synth, other_region: Synth
) -> None:
    """The single IAM5 ``Resource::*`` suppression cannot name the actions it covers, so a new
    statement such as dynamodb:Scan or kms:Decrypt on ``*`` would pass synthesis. This test is the
    allowlist (P2-010)."""
    found: set[str] = set()
    for synth_result in (default, other_region):
        for lid, st in _star_statements(synth_result):
            actions = st.get("Action")
            for action in actions if isinstance(actions, list) else [actions]:
                assert action in nag.STAR_RESOURCE_ACTIONS, (lid, action)
                found.add(action)
    assert found, (
        "no Resource:* statement found: the test would pass for the wrong reason"
    )
    # Every allowlisted action is really used, so the list cannot quietly grow stale.
    assert found == set(nag.STAR_RESOURCE_ACTIONS), (
        set(nag.STAR_RESOURCE_ACTIONS) - found
    )


def test_p2034_the_cfn_nag_policy_skips_name_the_policies_they_cover() -> None:
    assert "Backend" not in scanners._STAR_POLICIES  # not a bare prefix any more
    assert "Agents" not in scanners._STAR_POLICIES
    assert all(
        p.endswith(("DefaultPolicy", "Deployment")) for p in scanners._STAR_POLICIES
    )


def test_p2010_each_non_iam5_rule_is_suppressed_on_one_resource(default: Synth) -> None:
    """A stack suppression for APIG4 would cover a second unauthenticated route too."""
    for rule, path, _reason in nag.BY_PATH:
        assert path.count("/") >= 2, (rule, path)
    paths = {path for _rule, path, _reason in nag.BY_PATH}
    assert "Backend/Api/GET--api--config/Resource" in paths
    # Both dead-letter queues are named: the old reason mentioned only the indexer's.
    assert {"Backend/IndexerDlq/Resource", "Backend/FailedEvents/Resource"} <= paths
    routes = default.props("AWS::ApiGatewayV2::Route")
    unauthorized = [r for r in routes if not r.get("AuthorizerId")]
    assert [r["RouteKey"] for r in unauthorized] == ["GET /api/config"]


def test_p2075_the_web_search_gateway_offers_no_tool_search(default: Synth) -> None:
    """One connector, so the gateway's own tool-search tool would only be one more tool for the
    model to call, counted against the web-search cap (P2-075)."""
    (gateway,) = default.props("AWS::BedrockAgentCore::Gateway")
    mcp = gateway["ProtocolConfiguration"]["Mcp"]
    assert "SearchType" not in mcp
    assert mcp["SupportedVersions"] == ["2025-03-26"]


def test_p2029_the_chat_usage_function_touches_only_chat_budget_rows(
    default: Synth,
) -> None:
    statements = _statements(default, "BackendChatUsage")
    ddb = [
        st
        for st in statements
        if "dynamodb:" in json.dumps(st.get("Action")) and st["Effect"] == "Allow"
    ]
    assert ddb, "no DynamoDB statement found"
    for st in ddb:
        keys = st["Condition"]["ForAllValues:StringLike"]["dynamodb:LeadingKeys"]
        assert keys == ["CHAT", "CHATTURN#*", "BUDGET#chat", "BUDGET#chat-user-*"]
    # ForAllValues is true when the key is absent, so a Scan would slip through the condition.
    assert [
        st
        for st in statements
        if st["Effect"] == "Deny" and st.get("Action") == "dynamodb:Scan"
    ]
    assert not [st for st in ddb if "dynamodb:Scan" in json.dumps(st["Action"])]


def test_r13_the_source_watcher_touches_only_modules_and_its_checkpoints(
    default: Synth,
) -> None:
    """It parses untrusted pages: not the grants' rows beyond module definitions, the kill switch, budgets,
    chat ledgers or audit rows."""
    statements = _statements(default, "BackendWatch")
    ddb = [
        st
        for st in statements
        if "dynamodb:" in json.dumps(st.get("Action")) and st["Effect"] == "Allow"
    ]
    assert ddb, "no DynamoDB statement found"
    by_keys = {
        tuple(st["Condition"]["ForAllValues:StringLike"]["dynamodb:LeadingKeys"]): st[
            "Action"
        ]
        for st in ddb
    }
    assert set(by_keys) == {("MODULES", "MODULE#*"), ("WATCH#*",)}
    # Module definitions are read only: a page that took over the watcher cannot rewrite a module (r14).
    assert sorted(by_keys[("MODULES", "MODULE#*")]) == [
        "dynamodb:BatchGetItem",
        "dynamodb:ConditionCheckItem",
        "dynamodb:DescribeTable",
        "dynamodb:GetItem",
        "dynamodb:Query",
    ]
    assert "dynamodb:PutItem" in by_keys[("WATCH#*",)]
    # One Deny on Scan, once (code review r15: two calls attached it twice).
    assert (
        len(
            [
                st
                for st in statements
                if st["Effect"] == "Deny" and st.get("Action") == "dynamodb:Scan"
            ]
        )
        == 1
    )


def test_r14_the_crawler_touches_only_product_rows_and_the_owed_ingestion(
    default: Synth,
) -> None:
    """It parses untrusted documentation pages too."""
    statements = _statements(default, "BackendCrawler")
    ddb = [
        st
        for st in statements
        if "dynamodb:" in json.dumps(st.get("Action")) and st["Effect"] == "Allow"
    ]
    assert ddb, "no DynamoDB statement found"
    for st in ddb:
        assert st["Condition"]["ForAllValues:StringLike"]["dynamodb:LeadingKeys"] == [
            "PRODUCT#*",
            "KB#INGESTION",
        ]
    assert [
        st
        for st in statements
        if st["Effect"] == "Deny" and st.get("Action") == "dynamodb:Scan"
    ]


def test_p2030_the_tool_gate_cannot_list_the_document_keys(default: Synth) -> None:
    denies = [
        st
        for st in _statements(default, "BackendAgentTools")
        if st["Effect"] == "Deny" and "s3:ListBucket" in json.dumps(st["Action"])
    ]
    (deny,) = denies
    assert deny["Condition"]["StringNotLike"]["s3:prefix"] == [
        "radars",
        "radars/",
        "radars/*",
        "drafts",
        "drafts/",
        "drafts/*",
    ]


def test_p2073_the_chat_role_may_invoke_only_the_configured_chat_model(
    bundles: None,
) -> None:
    only = synth(chatModel="haiku-4-5")
    chat = json.dumps(_statements(only, "AgentsChatRuntime"))
    module = json.dumps(_statements(only, "AgentsModuleRuntime"))
    assert "claude-haiku-4-5" in chat
    for profile in agents.MODEL_PROFILES:
        assert profile in module, profile
        if "haiku-4-5" not in profile:
            assert profile not in chat, profile


def test_p2074_the_key_statements_name_this_deployments_resources(
    default: Synth,
) -> None:
    only = synth(monthlyBudgetUsd="100")
    (key,) = [
        r["Properties"]
        for k, r in only.resources("AWS::KMS::Key").items()
        if k.startswith("DataDataKey")
    ]
    by_sid = {s.get("Sid"): s for s in key["KeyPolicy"]["Statement"]}
    vectors = json.dumps(by_sid["AllowS3VectorsIndexing"]["Condition"])
    assert ":bucket/air-vectors-" in vectors  # the account and Region are Refs
    assert ":bucket/*" not in vectors
    budget = json.dumps(by_sid["AllowBudgetNotifications"]["Condition"])
    assert 'budget/air-insights-radar-us-east-1"' in budget.replace("\\", "")
    assert (
        "budget/air-insights-radar-us-east-1-account" in budget
        and "budget/*" not in budget
    )


def test_p2032_the_gateway_role_is_trusted_only_for_this_deployments_gateway(
    default: Synth,
) -> None:
    """The CDK L2's generated role adds an unconditioned bedrock-agentcore trust statement next to
    the conditioned one, which cancels it (P2-032)."""
    roles = [
        r["Properties"]["AssumeRolePolicyDocument"]["Statement"]
        for k, r in default.resources("AWS::IAM::Role").items()
        if "Gateway" in k
    ]
    assert roles, "no gateway role found"
    for statements in roles:
        assert len(statements) == 1, statements
        (statement,) = statements
        assert statement["Principal"] == {"Service": "bedrock-agentcore.amazonaws.com"}
        condition = json.dumps(statement["Condition"])
        assert "aws:SourceAccount" in condition
        # This deployment's gateway name exactly, then its id suffix (r3 P3-033).
        assert ':gateway/", {"Fn::Join": ["-", ["air-web"' in condition
        assert condition.endswith('"-*"]]}}}') and ":gateway/air-web*" not in condition


@pytest.mark.parametrize("memory_key", ["aws-owned", "data-key"])
def test_p3_033_and_p3_059_the_memory_role_trusts_both_of_this_deployments_memory_names(
    bundles: None, memory_key: str
) -> None:
    """The L2's memory/<name>* matched another deployment's memory, and trusting only the data-key
    name broke the old memory's writes while a deployment moved to it. The trust policy is updated in
    place on the same role (AgentsChatMemoryServiceRole*)."""
    s = synth(chatMemoryKey=memory_key)
    ((lid, role),) = [
        (k, r)
        for k, r in s.resources("AWS::IAM::Role").items()
        if "ChatMemoryServiceRole" in k
    ]
    assert lid.startswith("AgentsChatMemoryServiceRole")
    (statement,) = role["Properties"]["AssumeRolePolicyDocument"]["Statement"]
    arns = json.dumps(statement["Condition"]["ArnLike"]["aws:SourceArn"])
    assert ":memory/air_chat-*" in arns and ":memory/air_chat_k-*" in arns
    assert ":memory/air_chat*" not in arns and ":memory/air_chat_k*" not in arns
    assert statement["Condition"]["StringEquals"] == {
        "aws:SourceAccount": "111122223333"
    }


def test_p2069_the_waf_rate_limit_is_a_deploy_option(bundles: None) -> None:
    """An office behind one NAT address shares one limit, and since list pages follow every cursor a
    few people on the admin and audit pages can use it up (P2-069)."""
    (acl,) = synth().props("AWS::WAFv2::WebACL")
    (rate,) = [r for r in acl["Rules"] if r["Name"] == "RateLimitPerIp"]
    assert rate["Statement"]["RateBasedStatement"]["Limit"] == 2000
    # A throttled client is told so (429), not handed the firewall's 403 for blocked text (round 33 review).
    response = rate["Action"]["Block"]["CustomResponse"]
    assert (
        response["ResponseCode"] == 429
        and response["CustomResponseBodyKey"] == "rate_limited"
    )
    assert response["ResponseHeaders"] == [{"Name": "Retry-After", "Value": "300"}]
    body = json.loads(acl["CustomResponseBodies"]["rate_limited"]["Content"])
    assert (
        acl["CustomResponseBodies"]["rate_limited"]["ContentType"] == "APPLICATION_JSON"
    )
    assert (
        body["code"] == "rate_limited"
        and body["details"] == {"retry_after": 300}
        and "Wait" in body["message"]
    )
    (raised,) = synth(wafRateLimit="20000").props("AWS::WAFv2::WebACL")
    (rate,) = [r for r in raised["Rules"] if r["Name"] == "RateLimitPerIp"]
    assert rate["Statement"]["RateBasedStatement"]["Limit"] == 20000
    for bad in ("99", "2000000001", "lots"):
        with pytest.raises(ValueError, match="wafRateLimit"):
            DeployConfig.from_context(
                {"adminEmail": "a@example.com", "wafRateLimit": bad}.get
            )


def test_p2040_validation_can_run_the_default_customer_path() -> None:
    script = (ROOT / "deployment" / "validate.sh").read_text()
    assert 'RETAIN_DATA="${AIR_VALIDATE_RETAIN_DATA:-false}"' in script
    assert "deletion protection is on for" in script  # the protecting update really ran
    assert 'E2E_MARKERS="${AIR_VALIDATE_E2E_MARKERS:-not browser and not ai}"' in script
    assert '-m "$E2E_MARKERS"' in script
    workflow = (ROOT / ".github/workflows/deploy-validate.yml").read_text()
    assert "AIR_VALIDATE_RETAIN_DATA: ${{ inputs.retain_data }}" in workflow
    assert "AIR_VALIDATE_E2E_MARKERS: ${{ inputs.e2e_markers }}" in workflow


def test_p2009_and_p2031_no_role_may_delete_an_object_version(default: Synth) -> None:
    """Bucket versioning is the documented recovery path for a deleted document, and
    ``grant_delete`` includes DeleteObjectVersion, which removes the version a restore would read
    (P2-009). The sweep only adds radar versions, so it may not delete at all (P2-031)."""
    denies: dict[str, Any] = {}
    sweep_denies: list[str] = []
    for role in ("BackendApiHandler", "BackendCrawler", "BackendSweep"):
        for st in _statements(default, role):
            if st["Effect"] == "Deny" and "DeleteObjectVersion" in json.dumps(
                st["Action"]
            ):
                denies[role] = st
                if role == "BackendSweep":
                    sweep_denies.append(json.dumps(st["Resource"]))
    assert set(denies) == {"BackendApiHandler", "BackendCrawler", "BackendSweep"}
    assert denies["BackendApiHandler"]["Action"] == [
        "s3:DeleteObjectVersion",
        "s3:DeleteObjectVersionTagging",
    ]
    assert json.dumps(denies["BackendCrawler"]["Resource"]).endswith('"/docs/*"]]}')
    # The sweep may delete no object version, on radars/*, drafts/* or docs/*.
    joined = " ".join(sweep_denies)
    assert '/drafts/*"' in joined and '/radars/*"' in joined and '/docs/*"' in joined
    # The sweep gets put, not write, on radars/*. Its object deletes are a queued content idea's drafts and
    # the objects of uploads that were never completed (docs/*); without the docs/* grant those failed.
    deletable = set()
    for st in _statements(default, "BackendSweep"):
        if st["Effect"] == "Allow" and "s3:DeleteObject" in json.dumps(st["Action"]):
            resource = json.dumps(st["Resource"])
            assert "/radars/" not in resource, st
            deletable |= {p for p in ("/drafts/*", "/docs/*") if p in resource}
    assert deletable == {"/drafts/*", "/docs/*"}


def test_p2083_and_p2084_the_e2e_clients_refresh_their_tokens() -> None:
    """The live suite is not run here, so this is the guard: a client that holds the token it was
    built with fails late tests with 401 (P2-083), and a bare 403 cannot tell a Lambda refusal from
    a backend one (P2-084)."""
    session = (ROOT / "e2e/session.py").read_text()
    assert "token: Callable[[], str] | None = None" in session
    assert (
        'return {"authorization": f"Bearer {self._token()}"} if self._token else None'
        in session
    )
    # Raw responses (an export) are signed the same way: client.get alone sent no token (r3 e2e 401).
    assert "headers=self._fresh()" in session and session.count("self._fresh()") >= 2
    conftest = (ROOT / "e2e/tests/conftest.py").read_text()
    assert "@pytest.fixture\ndef tokens(" in conftest  # not session-scoped any more
    assert "token=lambda role=role:" in conftest
    boundary = (ROOT / "e2e/tests/test_agent_boundary.py").read_text()
    assert "def _code(res: httpx.Response)" in boundary
    assert boundary.count("_code(") >= 3


def test_cr2_02_the_seed_can_migrate_the_graph_within_its_timeout(
    default: Synth,
) -> None:
    """The seed re-keys stored nodes and entity names once per release, so it needs the graph table
    and more than the 30 seconds that seeding modules alone took."""
    (seed,) = [
        p
        for p in default.props("AWS::Lambda::Function")
        if p.get("Handler") == "insights_radar.api.workers.seed"
    ]
    assert seed["Timeout"] == 900  # P3-013: Lambda's maximum
    graph = next(
        k for k in default.resources("AWS::DynamoDB::GlobalTable") if "Graph" in k
    )
    granted = [
        st
        for st in _statements(default, "BackendSeed")
        if graph in json.dumps(st.get("Resource"))
    ]
    assert granted and "dynamodb:UpdateItem" in _actions(granted)


def test_sr2_11_the_workspace_ceiling_is_a_deploy_option_and_is_alarmed() -> None:
    stack = synth(workspaceRatePerMinute="1200")
    (api,) = [
        r["Properties"]
        for k, r in stack.resources("AWS::Lambda::Function").items()
        if k.startswith("BackendApiHandler")
    ]
    assert api["Environment"]["Variables"]["AIR_WORKSPACE_RATE_PER_MINUTE"] == "1200"
    (flt,) = [
        f
        for f in stack.props("AWS::Logs::MetricFilter")
        if f["MetricTransformations"][0]["MetricName"] == "WorkspaceRateLimited"
    ]
    assert "rate.workspace_limit" in flt["FilterPattern"]
    assert any(
        "workspace-wide rate ceiling" in a["AlarmDescription"]
        for a in stack.props("AWS::CloudWatch::Alarm")
    )
    for bad in ("59", "100001", "lots"):
        with pytest.raises(ValueError, match="workspaceRatePerMinute"):
            DeployConfig.from_context(
                {"adminEmail": "a@example.com", "workspaceRatePerMinute": bad}.get
            )


def test_p3_013_the_seed_has_lambdas_longest_timeout_and_keeps_its_logical_id(
    default: Synth,
) -> None:
    """At 300 s a large graph timed the Seed's migrations out and rolled the update back (P3-013). Timeout
    is updated in place on the existing function: same logical id, no replacement."""
    seeds = {
        lid: r["Properties"]
        for lid, r in default.resources("AWS::Lambda::Function").items()
        if lid.startswith("BackendSeed") and "Handler" in r["Properties"]
    }
    ((_lid, seed),) = seeds.items()
    assert (
        seed["Timeout"] == 900 and seed["Handler"] == "insights_radar.api.workers.seed"
    )
    trigger = json.dumps(default.resources("Custom::Trigger"))
    assert '"Timeout": "900000"' in trigger or '"Timeout": 900000' in trigger


def test_p3_031_the_web_search_gateway_has_an_error_alarm(default: Synth) -> None:
    """A gateway that lost its connector or key grant fails every search inside the runtime while runs
    end succeeded (P3-031). The metric is AgentCore's per-gateway SystemErrors."""
    (alarm,) = [a for d, a in _alarms(default).items() if "web-search gateway" in d]
    ((namespace, name, dims, _),) = _alarm_metrics(alarm)
    assert (namespace, name) == ("AWS/Bedrock-AgentCore", "SystemErrors")
    assert dims[
        "Operation"
    ] == "InvokeGateway" and "AgentsWebSearchGateway" in json.dumps(dims["Resource"])
    assert alarm["Threshold"] == monitoring.GATEWAY_ERRORS
    without = synth(webSearch="false")
    assert not [d for d in _alarms(without) if "web-search gateway" in d]
    assert len(without.props("AWS::CloudWatch::Alarm")) == 45


def test_p3_030_the_cloudfront_5xx_rate_needs_a_request_floor(default: Synth) -> None:
    """Cdn5xxRate keeps its logical id; its metric becomes metric math, which AWS::CloudWatch::Alarm
    updates in place (P3-030)."""
    ((_lid, alarm),) = [
        (k, r["Properties"])
        for k, r in default.resources("AWS::CloudWatch::Alarm").items()
        if k.startswith("MonitoringCdn5xxRate")
    ]
    expression = next(m for m in alarm["Metrics"] if "Expression" in m)["Expression"]
    assert expression == f"IF(r > {monitoring.CDN_MIN_REQUESTS}, e, 0)"
    used = {
        m["MetricStat"]["Metric"]["MetricName"]: m["MetricStat"]["Stat"]
        for m in alarm["Metrics"]
        if "MetricStat" in m
    }
    assert used == {"5xxErrorRate": "Average", "Requests": "Sum"}


def test_p3_051_the_deploy_options_say_which_release_the_stack_runs(
    default: Synth,
) -> None:
    from insights_radar_infra.config import SOLUTION_VERSION

    assert SOLUTION_VERSION != "1.0.0"
    outputs = default.template.to_json()["Outputs"]
    options = json.loads(outputs["DeployOptions"]["Value"])
    assert options["solutionVersion"] == SOLUTION_VERSION
    assert f"v{SOLUTION_VERSION}" in default.template.to_json()["Description"]
    assert f"## [{SOLUTION_VERSION}]" in (ROOT / "CHANGELOG.md").read_text()


def test_hc011_the_andon_ends_a_queued_run_quietly(default: Synth) -> None:
    states = _definition(default)["States"]
    catches = states["RunModule"]["Catch"]
    # Checked in order: GenAiDisabled is matched before States.ALL sends a run to RunErrored.
    assert catches[0]["ErrorEquals"] == ["GenAiDisabled"]
    assert states[catches[0]["Next"]]["Type"] == "Succeed"
    assert (
        catches[-1]["ErrorEquals"] == ["States.ALL"]
        and catches[-1]["Next"] == "FinalizeFailed"
    )
    grants = [
        st
        for p in default.props("AWS::IAM::Policy")
        for st in p["PolicyDocument"]["Statement"]
        if _actions([st]) == {"states:SendTaskFailure"}
    ]
    assert len(grants) == 1 and "Ref" in json.dumps(
        grants[0]["Resource"]
    )  # the runs state machine only


def test_hc106_the_indexer_can_reindex(default: Synth) -> None:
    (fn,) = [
        f
        for f in default.resources("AWS::Lambda::Function").values()
        if f["Properties"]["Handler"] == "insights_radar.api.workers.index"
    ]
    assert fn["Properties"]["Timeout"] == 900
    role = fn["Properties"]["Role"]["Fn::GetAtt"][0]
    statements = [
        st
        for p in default.resources("AWS::IAM::Policy").values()
        if {"Ref": role} in p["Properties"].get("Roles", [])
        for st in p["Properties"]["PolicyDocument"]["Statement"]
    ]
    acts = _actions(statements)
    assert {
        "dynamodb:Scan",
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:DeleteItem",
    } <= acts


def test_a_new_guardrail_generation_recreates_a_guardrail_deleted_outside_the_stack(
    bundles: None,
) -> None:
    """A deployment, 2026-11-04: another tool's clean-up deleted the guardrail; the update could not recreate it, since the
    stack still held its id, and every module run failed until it was a new resource with a new name."""
    (first,) = synth().resources("AWS::Bedrock::Guardrail").items()
    assert first[0] == R1_GUARDRAIL and first[1]["Properties"]["Name"].endswith(
        "-guardrail"
    )
    second = synth(guardrailGeneration="2")
    ((logical, guardrail),) = second.resources("AWS::Bedrock::Guardrail").items()
    assert logical != R1_GUARDRAIL and logical.startswith("AgentsGuardrail2")
    assert guardrail["Properties"]["Name"].endswith("-guardrail-2")
    (version,) = second.props("AWS::Bedrock::GuardrailVersion")
    assert version["GuardrailIdentifier"] == {"Fn::GetAtt": [logical, "GuardrailId"]}
    for bad in (
        "0",
        0,
        "100",
        "two",
        "\uff12",
    ):  # 0 from cdk.json, fullwidth 2 (round 44 code review)
        with pytest.raises(ValueError, match="guardrailGeneration"):
            DeployConfig.from_context(
                {"adminEmail": "a@example.com", "guardrailGeneration": bad}.get
            )
    assert (
        DeployConfig.from_context(
            {"adminEmail": "a@example.com"}.get
        ).guardrail_generation
        == 1
    )


def test_numeric_options_refuse_zero_and_non_ascii_digits() -> None:
    """Round 45 code review: 0 from cdk.json was read as unset and took the default; fullwidth digits passed."""
    base = {"adminEmail": "a@example.com"}
    for key in ("chatMemoryDays", "wafRateLimit", "workspaceRatePerMinute"):
        for bad in (0, "\uff13\uff10"):
            with pytest.raises(ValueError, match=key):
                DeployConfig.from_context({**base, key: bad}.get)
    assert (
        DeployConfig.from_context(
            {**base, "monthlyBudgetUsd": 0}.get
        ).monthly_budget_usd
        == 0
    )
    # Retention went through int() (round 46 code review), and a leading zero is octal to deploy.sh.
    for key in ("logRetentionDays", "agentLogRetentionDays"):
        for bad in (" 30", "\uff13\uff10", "30.0", "030", 0):
            with pytest.raises(ValueError, match=key):
                DeployConfig.from_context({**base, key: bad}.get)
    with pytest.raises(ValueError, match="chatMemoryDays"):
        DeployConfig.from_context({**base, "chatMemoryDays": "030"}.get)
    with pytest.raises(ValueError, match="monthlyBudgetUsd"):
        DeployConfig.from_context({**base, "monthlyBudgetUsd": "\uff15"}.get)


def test_finalize_and_the_sweep_may_delete_graph_nodes(default: Synth) -> None:
    """Code review r50: finalize removes the entities a run that ended early left unlinked, and the sweep
    finishes the deletes an administrator queued; with read-only (sweep) or no (finalize) access to the graph
    table both failed with AccessDenied in a deployed stack, and only a log line said so."""
    for role in ("BackendFinalize", "BackendSweep"):
        allowed = [st for st in _statements(default, role) if st["Effect"] == "Allow"]
        graph = [st for st in allowed if "DataGraphTable" in json.dumps(st["Resource"])]
        assert {
            "dynamodb:DeleteItem",
            "dynamodb:PutItem",
            "dynamodb:GetItem",
        } <= _actions(graph), role
