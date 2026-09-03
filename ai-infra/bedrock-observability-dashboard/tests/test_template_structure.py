def test_required_parameters_exist(template):
    params = template["Parameters"]
    for name in ["DashboardName", "ModelInvocationLogGroup", "CloudTrailLogGroup"]:
        assert name in params, f"missing parameter {name}"


def test_server_errors_alarm_threshold_param(template):
    p = template["Parameters"]["ServerErrorsAlarmThreshold"]
    assert p["Type"] == "Number"
    assert p["Default"] == 1
    alarm = template["Resources"]["AlarmServerErrors"]["Properties"]
    thr = alarm["Threshold"]
    assert isinstance(thr, dict) and thr.get("Ref") == "ServerErrorsAlarmThreshold"


def test_dashboard_body_is_valid_json_all_tiers(body_tier1, body_tier2, body_tier3):
    for body in (body_tier1, body_tier2, body_tier3):
        assert isinstance(body["widgets"], list)
        assert len(body["widgets"]) > 0


def test_no_unresolved_substitution_tokens(body_tier1, body_tier2, body_tier3):
    import json
    for body in (body_tier1, body_tier2, body_tier3):
        rendered = json.dumps(body)
        assert "${" not in rendered, "unresolved CloudFormation substitution token"


def test_tenant_metadata_key_param_default_team(template):
    p = template["Parameters"]["TenantMetadataKey"]
    assert p["Default"] == "team"
    assert "AllowedPattern" in p


def test_section6_uses_default_tenant_key(body_tier2):
    logs = [w for w in body_tier2["widgets"] if w["type"] == "log"]
    top = [w for w in logs if "Top teams" in w["properties"].get("title", "")]
    assert top, "Top teams widget missing"
    q = top[0]["properties"]["query"]
    assert "requestMetadata.team as tenant" in q
    assert "ispresent(tenant)" in q
    assert "by tenant" in q


def test_section6_honors_custom_tenant_key():
    import json
    from scripts.render_dashboard import render
    body = json.loads(render(2, {"TenantMetadataKey": "cost_center"}))
    logs = [w for w in body["widgets"] if w["type"] == "log"]
    top = [w for w in logs if "Top teams" in w["properties"].get("title", "")]
    assert top
    q = top[0]["properties"]["query"]
    assert "requestMetadata.cost_center as tenant" in q
    assert "requestMetadata.team" not in q


def test_query_definitions_carry_tenant_token(template):
    # The three tenant-scoped saved queries must use the parameter token (so a
    # custom TenantMetadataKey is substituted at deploy time), with no stale
    # literal `requestMetadata.team` left in their Fn::Sub source.
    res = template["Resources"]
    for q in ("QueryTopTeamsTokens", "QueryGuardrailInterventions", "QueryLargePrompts"):
        qs = res[q]["Properties"]["QueryString"]
        assert isinstance(qs, dict) and "Fn::Sub" in qs, f"{q} QueryString must be Fn::Sub"
        sub = qs["Fn::Sub"]
        body = sub[0] if isinstance(sub, list) else sub
        assert "requestMetadata.${TenantMetadataKey} as tenant" in body, q
        assert "requestMetadata.team" not in body, f"{q} has stale literal team"

    # Non-tenant queries must NOT be Fn::Sub (they have no parameter token).
    for q in ("QueryTokensByModelOperation", "QueryStopReasonDistribution"):
        qs = res[q]["Properties"]["QueryString"]
        assert not isinstance(qs, dict), f"{q} should be a plain block scalar, not Fn::Sub"
