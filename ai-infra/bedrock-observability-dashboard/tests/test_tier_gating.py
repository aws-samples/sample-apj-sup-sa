def _markdowns(body):
    return [w["properties"].get("markdown", "")
            for w in body["widgets"] if w["type"] == "text"]


def test_tier1_has_no_section6_or_section7(body_tier1):
    md = " ".join(_markdowns(body_tier1))
    assert "6. Per-tenant attribution" not in md
    assert "7. Identity, routing" not in md
    assert all(w["type"] != "log" for w in body_tier1["widgets"])


def test_tier2_has_section6_not_section7(body_tier2):
    md = " ".join(_markdowns(body_tier2))
    assert "6. Per-tenant attribution" in md
    assert "7. Identity, routing" not in md
    assert any(w["type"] == "log" for w in body_tier2["widgets"])


def test_tier3_has_section6_and_section7(body_tier3):
    md = " ".join(_markdowns(body_tier3))
    assert "6. Per-tenant attribution" in md
    assert "7. Identity, routing" in md


def test_widget_count_deltas(body_tier1, body_tier2, body_tier3):
    n1, n2, n3 = (len(b["widgets"]) for b in (body_tier1, body_tier2, body_tier3))
    assert n2 - n1 == 5, f"Section 6 should add 5 widgets, got {n2 - n1}"
    assert n3 - n2 == 4, f"Section 7 should add 4 widgets, got {n3 - n2}"


def test_query_definitions_gated(template):
    res = template["Resources"]
    log_qs = ["QueryTopTeamsTokens", "QueryTokensByModelOperation",
              "QueryGuardrailInterventions", "QueryLargePrompts",
              "QueryStopReasonDistribution"]
    for q in log_qs:
        assert res[q].get("Condition") == "HasInvocationLogs", q
    ct_qs = ["QueryTopCallersByIdentity", "QueryThrottleReasons",
             "QueryCrossRegionRouting"]
    for q in ct_qs:
        assert res[q].get("Condition") == "HasCloudTrail", q


def test_log_delivery_alarm_gated(template):
    assert template["Resources"]["AlarmLogDeliveryFailures"].get("Condition") \
        == "HasInvocationLogs"


def test_query_names_are_stack_scoped(template):
    for name, res in template["Resources"].items():
        if res.get("Type") == "AWS::Logs::QueryDefinition":
            nm = res["Properties"]["Name"]
            assert isinstance(nm, dict) and "Fn::Sub" in nm, f"{name} name not scoped"
            assert "${DashboardName}" in nm["Fn::Sub"], f"{name} missing DashboardName"


def test_cloudtrail_eventsource_consistent(template, body_tier3):
    # All CloudTrail references (Section 7 widgets + saved QueryDefinitions) must
    # filter on the SAME verified eventSource. Mismatch = some widgets silently
    # return no rows. The expected value is the management-event source
    # (bedrock.amazonaws.com), verified empirically via CloudTrail lookup-events.
    import re
    sources = set()
    # body_tier3 is JSON-decoded, so query strings have plain (unescaped) quotes.
    for w in body_tier3["widgets"]:
        q = w.get("properties", {}).get("query", "")
        for m in re.findall(r'eventSource = "([^"]+)"', q):
            sources.add(m)
    for name, res in template["Resources"].items():
        if res.get("Type") == "AWS::Logs::QueryDefinition" and res.get("Condition") == "HasCloudTrail":
            qs = res["Properties"]["QueryString"]
            body = qs if isinstance(qs, str) else (qs.get("Fn::Sub") if isinstance(qs, dict) else "")
            if isinstance(body, list):
                body = body[0]
            for m in re.findall(r'eventSource = "([^"]+)"', body):
                sources.add(m)
    assert len(sources) == 1, f"inconsistent CloudTrail eventSource values: {sources}"
