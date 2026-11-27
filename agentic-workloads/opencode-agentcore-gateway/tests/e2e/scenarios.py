# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""End-to-end scenario matrix for a deployed stack.

Each scenario is one call (or a short sequence) made as a real user against the live gateway, the local ocgw proxy,
the web search MCP gateway, the dashboard or opencode itself, with the expected outcome. `scenarios()` builds the full
list; tests/e2e/run.py executes it and writes tests/e2e/out/results.json.

Subfeature IDs (F<area>.<n>) map to the feature inventory in docs/testing.md.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

USERS = {  # test user -> (groups, budget note)
    "alice": ["ocgw-team-a"],
    "bob": ["ocgw-team-b"],
    "carol": ["ocgw-team-b"],  # tiny budget and 2 rpm by default
}
MODELS = ["ow/kimi-k3", "ow/glm-5.3", "ow/deepseek-v3.2", "ow/glm-5"]
TEAM_MODELS = {"ocgw-team-a": {"ow/kimi-k3", "ow/glm-5.3"}, "ocgw-team-b": set(MODELS)}
ROUTES = ["chat", "responses"]
IDPS = ["cognito", "okta", "jumpcloud"]


@dataclass
class Scenario:
    id: str
    feature: str          # subfeature ID, e.g. F4.1
    kind: str             # http | proxy | mcp | dashboard | opencode | idp | terraform
    user: str | None
    request: dict
    expect: dict
    idp: str = "cognito"
    tags: list = field(default_factory=list)


def allowed(user, model):
    return any(model in TEAM_MODELS[g] for g in USERS[user])


def scenarios():
    out = []

    def add(feature, kind, user, request, expect, *tags, idp="cognito"):
        out.append(Scenario(f"{feature}-{len(out):04d}", feature, kind, user, request, expect, idp, list(tags)))

    # F3/F4: every user x model x API x streaming x tools x reasoning effort: allowed -> 200 from the real model.
    for idp, user, model, route, stream, tools, effort in itertools.product(
            IDPS, ["alice", "bob"], MODELS, ROUTES, [False, True], [False, True], [None, "low", "high"]):
        if route == "responses" and model in ("ow/deepseek-v3.2", "ow/glm-5"):
            continue  # Chat Completions only (model card)
        ok = allowed(user, model)
        sub = f"F3.{model[3:]}.{route}.{'stream' if stream else 'once'}" if ok else f"F4.deny.{model[3:]}"
        add(sub, "http", user, {"route": route, "model": model, "stream": stream, "tools": tools, "effort": effort},
            {"status": 200 if ok else 403, "has_text_or_tool": ok}, "model", idp=idp)

    # F4: aliases, unknown and odd model strings.
    for idp, user, model in itertools.product(IDPS, ["alice", "bob"], ["kimi", "ow/unknown", "gpt-4o", "OW/kimi-k3", " ow/kimi-k3", ""]):
        exp = 200 if model == "kimi" else (400 if model == "" else 404)
        add(f"F4.model.{model.strip().lower().replace('/', '_') or 'empty'}", "http", user, {"route": "chat", "model": model}, {"status": exp}, idp=idp)

    # F1: identity and token edge cases (no token, bad signature, expired, wrong audience, wrong issuer, no groups).
    # Codes seen on the live gateway: no token or a non-JWT is 401; a JWT that fails validation (signature, expiry,
    # audience, issuer, forged claims) is 403 insufficient_scope; two Authorization headers are a 400 at the front end.
    for case, status in [("none", 401), ("garbage", 401), ("tampered_sig", 403), ("expired", 403), ("wrong_aud", 403),
                         ("wrong_iss", 403), ("other_pool_user", 403), ("duplicate_auth_header", 400), ("basic_auth", 401)]:
        for idp, route in itertools.product(IDPS, ROUTES + ["models"]):
            add(f"F1.token.{case}", "http", "alice", {"route": route, "model": "ow/kimi-k3", "token": case}, {"status": status}, idp=idp)
    for idp, case in itertools.product(IDPS, ["groups_claim_list", "groups_claim_string", "no_groups_claim", "unknown_group",
                                               "two_groups_union", "group_removed_in_idp", "user_disabled_in_idp"]):
        add(f"F1.groups.{case}", "http", "bob", {"route": "chat", "model": "ow/deepseek-v3.2", "groups_case": case},
            {"status": 200 if case in ("groups_claim_list", "groups_claim_string", "two_groups_union") else 403}, idp=idp)

    # F8: request hardening, for each route. get_on_chat is a 400 (no body, so no model): the gateway reports every
    # inference call as POST, so the interceptor routes on the path and the empty body is refused before any target.
    bad = [("not_json", 400), ("json_array", 400), ("nan", 400), ("infinity", 400), ("1e999", 400), ("deep_nesting", 400),
           ("model_int", 400), ("model_null", 400), ("model_list", 400), ("dup_model_key", 200), ("huge_body_6mb", 413),
           ("spoof_openai_project", 200), ("spoof_x_ocgw_user", 200), ("encoded_path", 200), ("double_encoded_path", 404),
           ("dot_segments", 404), ("unknown_route", 404), ("invoke_path", 404), ("get_on_chat", 400)]
    for (case, status), route, idp in itertools.product(bad, ROUTES, IDPS):
        add(f"F8.{case}", "http", "bob", {"route": route, "model": "ow/kimi-k3", "attack": case}, {"status": status}, idp=idp)

    # F8: another user's stored response must not be readable or continuable (previous_response_id), and a
    # method-override header must not turn a POST into something else.
    # The override runs on the chat route, the one every model serves, so the POST reaches a model and the header
    # can be shown to have changed nothing; the stored-response case must not return another user's response.
    f8 = {"previous_response_id_of_other_user": ("responses", {"status_not": 200}),
          "x_http_method_override_delete": ("chat", {"status_in": [200, 400]})}
    for idp, case in itertools.product(IDPS, sorted(f8)):
        route, expect = f8[case]
        add(f"F8.{case}", "http", "bob", {"route": route, "model": "ow/kimi-k3", "xuser": case}, expect, idp=idp)

    # F5: budgets per window, as carol (tiny budget) and with admin-set caps.
    for window, cap, spent_state, idp in itertools.product(["Monthly", "Daily", "Hourly"], [0, 0.01, 5], ["under", "at", "over"], IDPS):
        exp = 200 if (spent_state == "under" and cap > 0) else 400
        add(f"F5.{window.lower()}.{spent_state}", "http", "bob", {"route": "chat", "model": "ow/glm-5", "set_cap": {window: cap}, "spent": spent_state},
            {"status": exp, "code": None if exp == 200 else "budget_exceeded"}, idp=idp)

    # F6: rate limits: interceptor rpm and gateway RPM/TPM, per user, per model, recovery after a minute. The rpm cases
    # run as bob: carol has a gateway limit of 2 per minute and a $0.02 budget (variables.tf), which would answer first.
    for rpm, burst, idp in itertools.product([1, 2, 5], [1, 3, 8], IDPS):
        add(f"F6.rpm.{rpm}", "http", "bob", {"route": "chat", "model": "ow/glm-5", "set_rpm": rpm, "burst": burst},
            {"allowed": min(rpm, burst), "rejected_429": max(0, burst - rpm)}, idp=idp)
    # The gateway's limit is a refilling bucket enforced across its fleet, so a burst just past the limit can pass;
    # limit + 6 is clearly over it (r126: limit + 2 split 200/200/200 vs one 429 between runs).
    # gateway_rpm runs against carol's standing limit (rpm_overrides, 2 a minute) without editing the rule: r183 showed
    # that after an edit the gateway lets everything through for minutes, even with the rule ACTIVE (a limit of 1
    # bit only in the third minute; a limit of 3 set right after it never bit in three). That tests what Terraform
    # deploys. gateway_tpm still edits the rule (carol's standing tokens limit is far above a test burst), and has
    # been enforced in the first minute every time.
    # Bursts well past the limit: the gateway's buckets are spread over its fleet, so at 2 a minute 8 calls passed
    # whole in r186 while 12 drew three 429s.
    for burst in [12, 16]:
        add("F6.gateway_rpm", "http", "carol", {"route": "chat", "model": "ow/glm-5", "native_limit": {"gateway_rpm": None}, "burst": burst},
            {"some_429": True})
    # r195: which runs first, the gateway's rate limit or the REQUEST interceptor? AgentCore documents rate limits
    # before gateway rules but says nothing about interceptors. A rate of 0 blocks all of the user's traffic
    # (devguide: gateway-rate-limits.html); a call for a model the gateway does not serve then answers with the
    # gateway's 429 if the limit runs first, or the interceptor's 404 if the interceptor does. Either is a pass: the
    # scenario measures the order and reports it. carol has her own entry in the rule (rpm_overrides).
    add("F6.gateway_order", "http", "carol", {"route": "chat", "model": "ow/not-served", "gateway_order": True}, {"order_known": True})
    for limit in [1, 3]:
        add("F6.gateway_tpm", "http", "carol", {"route": "chat", "model": "ow/glm-5", "native_limit": {"gateway_tpm": limit}, "burst": limit + 6},
            {"some_429": True})

    # F7: fallback: primary forced to fail (throttle, 5xx, timeout) -> served by the fallback; chain end -> error.
    for fault, user, idp in itertools.product(["throttle", "5xx", "first_byte_timeout", "unreachable"], ["alice", "bob"], IDPS):
        add(f"F7.{fault}", "http", user, {"route": "chat", "model": "ow/glm-5.3", "fault": fault},
            {"status": 200, "served_by": "moonshotai.kimi-k3"}, idp=idp)
    # A 404 from the primary (model missing) is not a fallback trigger: the caller gets the 404, no hop is tried.
    for user, idp in itertools.product(["alice", "bob"], IDPS):
        add("F7.model_missing", "http", user, {"route": "chat", "model": "ow/glm-5.3", "fault": "model_missing"}, {"status": 404}, idp=idp)

    # F9: web search via the MCP gateway (opencode's remote MCP) and the /websearch helper.
    for user, q, idp in itertools.product(["alice", "bob", "nogroup"], ["aws re:invent 2026 dates", "", "x" * 2001], IDPS):
        add(f"F9.query.{'ok' if q and len(q) <= 2000 else ('empty' if not q else 'too_long')}", "mcp", user, {"tool": "web___WebSearch", "query": q}, {"ok": user != "nogroup" and bool(q) and len(q) <= 2000}, idp=idp)

    # F9: the same search through the gateway's /inference/websearch helper route (the interceptor calls the MCP
    # gateway itself; r56 broke only this path), as each user, with good and bad queries.
    for user, q, idp in itertools.product(["alice", "bob", "nogroup"], ["aws re:invent 2026 dates", ""], IDPS):
        add(f"F9.helper.{'ok' if q else 'empty'}", "websearch", user, {"q": q},
            {"status": 200 if (user != "nogroup" and q) else (400 if q == "" and user != "nogroup" else 403)}, idp=idp)

    # F10: attribution after usage_sync: per user, model, group, tag; token counts match model usage.
    for user, model, idp in itertools.product(["alice", "bob"], ["ow/kimi-k3", "ow/glm-5.3"], IDPS):
        add(f"F10.{model[3:]}", "http", user, {"route": "chat", "model": model, "then": "wait_usage_sync"},
            {"ledger_user_spend_up": True, "ledger_model_rollup_up": True, "ledger_group_rollup_up": True}, idp=idp)

    # F2: the client: login, token, refresh, proxy, opencode-config, logout.
    for case in ["login_pkce", "token_refresh", "rotated_refresh", "proxy_wrong_key", "proxy_path_denied", "proxy_stream",
                 "opencode_config_models", "logout_then_token", "two_proxies_same_port", "mdm_overrides_env"]:
        for idp in IDPS:
            add(f"F2.client.{case}", "proxy", "alice", {"case": case}, {"ok": True}, idp=idp)

    # F2: the real opencode CLI, headless, as each user and model.
    for idp, user, model in itertools.product(IDPS, ["alice", "bob"], MODELS):
        add(f"F2.opencode.{model[3:]}", "opencode", user, {"prompt": "Reply with the single word PONG", "model": f"ocgw/{model}"},
            {"pong": allowed(user, model)}, idp=idp)
    for user, task in itertools.product(["bob"], ["write_file", "run_shell", "multi_tool_edit", "compact", "switch_model_mid_session"]):
        add(f"F2.task.{task}", "opencode", user, {"task": task, "model": "ocgw/ow/kimi-k3"}, {"ok": True})

    # F11: dashboard as an admin and as a viewer, in the browser.
    for idp, role, page in itertools.product(IDPS, ["admin", "viewer", "not_signed_in"],
                                        ["overview", "users", "user_detail", "groups", "budgets", "rate_limits", "model_access", "audit", "usage"]):
        add(f"F11.page.{page}", "dashboard", None, {"role": role, "page": page}, {"visible": role != "not_signed_in"}, idp=idp)
    for action in ["set_user_budget", "set_group_budget", "set_rpm", "allow_model", "deny_model", "block_message",
                   "group_with_space", "double_click_save", "concurrent_edit", "invalid_number", "repair_record", "repair_readable_record"]:
        add(f"F11.action.{action}", "dashboard", None, {"role": "admin", "action": action},
            {"ok": action not in ("invalid_number", "repair_readable_record")})

    # F12: infrastructure checks.
    for check in ["plan_no_changes", "kms_on_logs", "ddb_pitr", "lambda_tracing", "iam_least_privilege", "destroy_clean"]:
        add(f"F12.{check}", "terraform", None, {"check": check}, {"ok": True})
    return out


if __name__ == "__main__":
    s = scenarios()
    feats = sorted({x.feature for x in s})
    print(f"{len(s)} scenarios, {len(feats)} subfeatures")
