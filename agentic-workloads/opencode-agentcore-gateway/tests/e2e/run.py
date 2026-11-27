#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Run the end-to-end matrix (scenarios.py) against a deployed stack and write tests/e2e/out/results.json.

  python3 tests/e2e/run.py --stack terraform [--idp cognito] [--only F3.] [--dry-run] [--limit N]

Tokens: for idp=cognito the runner signs each test user in with Cognito USER_PASSWORD_AUTH using the passwords from
`terraform output -json passwords`; for okta and jumpcloud it runs `ocgw token` in the home of a test user signed in
once with `ocgw login` (OCGW_HOME_<USER>_<IDP>=/path/to/that home's .config/ocgw), which refreshes the ID token when
it nears expiry. Each scenario kind has a driver; a kind without a driver is
reported as "no_driver", never as a pass. A scenario whose capability the platform does not offer (today: the
/v1/responses API, which no open-weight model serves) is reported as "n/a" with the upstream refusal, so it is
neither counted as a pass nor read as a regression in this sample.
"""
from __future__ import annotations

import argparse
import atexit
import base64
import contextlib
import http.client
import json
import os
import shutil
import signal
import socket
import subprocess  # nosec B404 - runs terraform output only, fixed argv
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scenarios as S  # noqa: E402

sys.path.insert(1, str(HERE.parent.parent / "lambda"))
import policy  # noqa: E402  - the gateway's own spend rules (window_read, window_sk), so the test sums as it does

OUT = HERE / "out"


def tf_outputs(stack: Path) -> dict:
    raw = subprocess.run(["terraform", f"-chdir={stack}", "output", "-json"], check=True, capture_output=True, text=True).stdout  # nosec B603 B607
    return {k: v["value"] for k, v in json.loads(raw).items()}


def cognito_sign_in(outs: dict, region: str, user: str) -> dict:
    """A fresh Cognito sign-in for a test user (USER_PASSWORD_AUTH, uncached): the AuthenticationResult. The one place
    the runner builds that call, so a change to the flow (a client secret, another auth flow) is made once."""
    import boto3  # noqa: PLC0415
    return boto3.client("cognito-idp", region_name=region).initiate_auth(
        ClientId=outs["cognito_client_id"], AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": user, "PASSWORD": outs["passwords"][user]})["AuthenticationResult"]


class Tokens:
    def __init__(self, outs: dict, region: str):
        self.outs, self.region, self.cache, self.until = outs, region, {}, {}

    def forget(self, user: str) -> None:
        """Drop a user's cached tokens, so the next call signs in afresh (after Cognito revoked them)."""
        for k in [k for k in self.cache if k[0] == user]:
            del self.cache[k]

    def get(self, user: str, idp: str) -> str:
        if (user, idp) in self.cache and time.time() < self.until.get((user, idp), float("inf")):
            return self.cache[(user, idp)]
        if idp != "cognito":
            # `ocgw token` from the user's signed-in home: it refreshes (rotating the refresh token under its file lock)
            # when the ID token is near expiry, so a run longer than the token's hour keeps working. Kept here until
            # five minutes before its exp, so most calls start no subprocess.
            env = {"HOME": str(signed_home(user, idp)), "PATH": os.environ.get("PATH", "")}
            r = py_run([str(CLIENT), "token"], env, capture_output=True, text=True, timeout=120)
            if r.returncode != 0 or r.stdout.count(".") != 2:
                raise LookupError(f"ocgw token for {user} on {idp} failed: {(r.stderr or r.stdout)[-300:]}")
            tok = r.stdout.strip()
            self.cache[(user, idp)] = tok
            self.until[(user, idp)] = _jwt_exp(tok) - 300
            return tok
        tok = cognito_sign_in(self.outs, self.region, user)["AccessToken"]
        self.cache[(user, idp)] = tok
        return tok


def _jwt_exp(tok: str) -> float:
    body = tok.split(".")[1]
    return float(json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))).get("exp") or 0)


def ocgw_config_for(ctx, user: str, idp: str) -> dict:
    """The ocgw config a developer of this stack would have. Cognito: the stack's ocgw_config output. Okta and
    JumpCloud (where Terraform cannot know the user's IdP sign-in and outputs none): the signed-in user's own."""
    if idp == "cognito":
        return json.loads(ctx.outs["ocgw_config"])
    cfg = json.loads((signed_home(user, idp) / ".config" / "ocgw" / "config.json").read_text())
    cfg.pop("proxy_port", None)
    return cfg


def signed_home(user: str, idp: str, tag: str = "") -> Path:
    """The HOME of a test user signed in to Okta or JumpCloud with `ocgw login` (OCGW_HOME_<USER>_<IDP> names its
    .config/ocgw directory; OCGW_HOME_<USER>_<IDP>_<TAG> a second sign-in for the client cases that spend or remove
    the tokens). Interactive sign-ins cannot be scripted against a real IdP, so they are done once, by hand, before the
    run."""
    var = f"OCGW_HOME_{user.upper()}_{idp.upper()}" + (f"_{tag.upper()}" if tag else "")
    d = os.environ.get(var)
    if not d:
        raise LookupError(f"set {var} to the .config/ocgw of {user} signed in on {idp}")
    if not (Path(d) / "tokens.json").exists():  # never signed in, or spent: the logout case removes this sign-in
        raise LookupError(f"{var} ({d}) is not signed in: run `ocgw login` there again"
                          + (" (each run's logout_then_token case signs this home out)" if tag else ""))
    return Path(d).resolve().parent.parent


def bad_token(case: str, good: str) -> dict:
    """Authorization header(s) for the F1.token.* cases."""
    head, body, sig = (good.split(".") + ["", "", ""])[:3]
    c = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)) or b"{}") if body else {}

    def forge(**kw):
        p = base64.urlsafe_b64encode(json.dumps({**c, **kw}).encode()).decode().rstrip("=")
        return f"{head}.{p}.{sig}"
    return {
        "none": {}, "garbage": {"Authorization": "Bearer garbage"}, "basic_auth": {"Authorization": "Basic YTpi"},
        "tampered_sig": {"Authorization": f"Bearer {head}.{body}.{sig[:-4]}AAAA"},
        "expired": {"Authorization": f"Bearer {forge(exp=int(time.time()) - 60)}"},
        "wrong_aud": {"Authorization": f"Bearer {forge(aud='someone-else', client_id='someone-else')}"},
        "wrong_iss": {"Authorization": f"Bearer {forge(iss='https://evil.example.com/')}"},
        "other_pool_user": {"Authorization": f"Bearer {forge(username='mallory', email='mallory@example.com')}"},
        # A list of pairs, so both headers go on the wire (a dict, or urllib.request, would fold them into one).
        "duplicate_auth_header": [("Authorization", f"Bearer {good}"), ("authorization", f"Bearer {good}")],
    }[case]


def responses_unserved(sc: S.Scenario, status: int, body: str) -> bool:
    """True when this scenario needs /v1/responses and the model refuses that route. No open-weight model on Bedrock
    serves the responses API (probed r126: every configured model answers "does not support the '/v1/responses' API.
    Try '/v1/chat/completions' instead."), so such a scenario is reported n/a, not as a pass and not as a failure."""
    return sc.request.get("route") == "responses" and status == 400 and "/v1/responses" in body


NA_RESPONSES = "the model does not serve the /v1/responses API"


def body_for(sc: S.Scenario) -> dict:
    r = sc.request
    msgs = [{"role": "user", "content": "Reply with the single word PONG."}]
    # 1024: a reasoning model can spend 256 tokens thinking and stream no answer (r138: GLM, a 200 with no text).
    if r["route"] == "responses":
        b = {"model": r["model"], "input": msgs, "max_output_tokens": 1024}
    else:
        b = {"model": r["model"], "messages": msgs, "max_tokens": 1024}
    if r.get("stream"):
        b["stream"] = True
    if r.get("tools"):
        fn = {"name": "get_time", "description": "Current time", "parameters": {"type": "object", "properties": {}}}
        b["tools"] = [{"type": "function", **({"function": fn} if r["route"] == "chat" else fn)}]
    if r.get("effort"):
        if r["route"] == "responses":
            b["reasoning"] = {"effort": r["effort"]}
        else:
            b["reasoning_effort"] = r["effort"]
    return b


def http_call(base: str, path: str, method: str, headers, data: bytes | None, timeout=300):
    """One HTTPS request. headers is a dict or a list of (name, value) pairs; every pair is sent as given, so a
    repeated header really is repeated on the wire."""
    u = urllib.parse.urlsplit(base + path)
    if u.scheme != "https":
        raise ValueError(f"{base} is not https")
    pairs = [("Content-Type", "application/json"), *(headers.items() if isinstance(headers, dict) else headers)]
    c = http.client.HTTPSConnection(u.netloc, timeout=timeout)
    try:
        c.putrequest(method, (u.path or "/") + (f"?{u.query}" if u.query else ""))
        for k, v in pairs:
            c.putheader(k, v)
        if data is not None:
            c.putheader("Content-Length", str(len(data)))
        c.endheaders(data)
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


E2E_GROUP = "ocgw-e2e-unknown"  # a Cognito group no policy or GROUP_MODELS entry names; created and removed here


RECORDED = ("token_groups", "groups_iat", "groups_seen_at")  # what the interceptor records on BUDGET (groups_seen_at: r129-r130)


def _restore_recorded_groups(ctx, user, bkey, seen):
    """Put the user's recorded groups (RECORDED attributes of BUDGET) back exactly as before the scenario: each one
    set to its old value, or removed if it had none. The interceptor writes them in the request, before the reply,
    so once the scenario's call is answered no write of it can land later and undo this (r128 saw one do that)."""
    sets = [a for a in RECORDED if a in seen]
    expr = " ".join(p for p in (("SET " + ", ".join(f"{a} = :{a}" for a in sets)) if sets else "",
                                ("REMOVE " + ", ".join(a for a in RECORDED if a not in seen)) if len(sets) < len(RECORDED) else "") if p)
    kw = {"ExpressionAttributeValues": {f":{a}": seen[a] for a in sets}} if sets else {}
    _ddb(ctx).update_item(TableName=ctx.table, Key=bkey, UpdateExpression=expr, ConditionExpression="attribute_exists(pk)", **kw)


def drive_groups(sc: S.Scenario, ctx):
    """F1.groups.*: change the user's IdP groups or account, sign in afresh, call, restore (see r128's notes)."""
    case = sc.request["groups_case"]
    if sc.idp != "cognito":
        return None, {"reason": "group changes are driven on Cognito; the Okta and JumpCloud sets need their admin APIs"}
    if case == "groups_claim_string":
        return "n/a", {"reason": "Cognito always sends cognito:groups as a list; a string claim is covered by the unit "
                                 "suite and the Okta/JumpCloud sets"}
    import boto3  # noqa: PLC0415
    c = boto3.client("cognito-idp", region_name=ctx.region)
    pool, user = ctx.outs["cognito_user_pool_id"], sc.user
    before = sorted(g["GroupName"] for g in c.admin_list_groups_for_user(UserPoolId=pool, Username=user)["Groups"])
    # The interceptor records the groups it sees on the user's BUDGET item (RECORDED); the call below writes the
    # test's groups there, so those attributes are put back as they were too.
    bkey = {"pk": {"S": f"USER#{user}"}, "sk": {"S": "BUDGET"}}
    seen = {a: v for a, v in (_ddb(ctx).get_item(TableName=ctx.table, Key=bkey, ConsistentRead=True).get("Item") or {}).items()
            if a in RECORDED}
    made_group = disabled = failed = False
    try:
        if case == "two_groups_union":
            c.admin_add_user_to_group(UserPoolId=pool, Username=user, GroupName="ocgw-team-a")
        elif case == "group_removed_in_idp":
            # Keep a group that does not allow the model and remove the ones that do: their models must go with them,
            # not linger from the user's earlier tokens (no_groups_claim covers removing every group).
            c.admin_add_user_to_group(UserPoolId=pool, Username=user, GroupName="ocgw-team-a")
            for g in before:
                if g != "ocgw-team-a":
                    c.admin_remove_user_from_group(UserPoolId=pool, Username=user, GroupName=g)
        elif case in ("no_groups_claim", "unknown_group"):
            for g in before:
                c.admin_remove_user_from_group(UserPoolId=pool, Username=user, GroupName=g)
            if case == "unknown_group":
                made_group = True  # ours either way: one an interrupted earlier run left behind is removed here too
                try:
                    c.create_group(UserPoolId=pool, GroupName=E2E_GROUP, Description="ocgw e2e: a group no policy names")
                except c.exceptions.GroupExistsException:
                    pass
                c.admin_add_user_to_group(UserPoolId=pool, Username=user, GroupName=E2E_GROUP)
        elif case == "user_disabled_in_idp":
            disabled = True
            c.admin_disable_user(UserPoolId=pool, Username=user)
            try:
                cognito_sign_in(ctx.outs, ctx.region, user)
            except c.exceptions.NotAuthorizedException as e:
                return True, {"refused_sign_in": str(e)[:160]}
            return False, {"reason": "a disabled user was issued a new token"}
        tok = cognito_sign_in(ctx.outs, ctx.region, user)["AccessToken"]
        st, raw = http_call(ctx.base, "/inference/v1/chat/completions", "POST", {"Authorization": f"Bearer {tok}"},
                            json.dumps(body_for(sc)).encode())
        return st == sc.expect["status"], {"status": st, "groups_before": before, "body": raw[:200].decode(errors="replace")}
    except BaseException:
        failed = True
        raise
    finally:  # bob exactly as he was: enabled, in the same groups, the same recorded groups, no test group in the pool
        c.admin_enable_user(UserPoolId=pool, Username=user)
        now = {g["GroupName"] for g in c.admin_list_groups_for_user(UserPoolId=pool, Username=user)["Groups"]}
        for g in now - set(before):
            c.admin_remove_user_from_group(UserPoolId=pool, Username=user, GroupName=g)
        for g in set(before) - now:
            c.admin_add_user_to_group(UserPoolId=pool, Username=user, GroupName=g)
        if made_group:
            try:
                c.delete_group(UserPoolId=pool, GroupName=E2E_GROUP)
            except c.exceptions.ResourceNotFoundException:
                pass
        if disabled:  # AdminDisableUser revokes the user's tokens: later scenarios sign in afresh
            ctx.tokens.forget(user)
            for k in [k for k in _HOMES if k[0] == user]:
                _HOMES.pop(k)[1].terminate()
        try:  # last: a DynamoDB error here cannot skip the cleanup above
            _restore_recorded_groups(ctx, user, bkey, seen)
        except Exception as e:
            if not failed:
                raise  # the scenario itself went through: report the restore failure
            print(f"warning: {user}'s recorded groups were not restored ({e}); the scenario's own error follows",
                  file=sys.stderr)  # never hide the scenario's exception behind this one


def drive_http(sc: S.Scenario, ctx) -> tuple[bool, dict]:
    r = sc.request
    if "groups_case" in r:
        return drive_groups(sc, ctx)
    if "attack" in r:
        return drive_attack(sc, ctx)
    if "xuser" in r:
        return drive_xuser(sc, ctx)
    if "set_cap" in r:
        return drive_budget(sc, ctx)
    if "set_rpm" in r:
        return drive_rpm(sc, ctx)
    if "fault" in r:
        return drive_fallback(sc, ctx)
    if r.get("gateway_order"):
        return drive_gateway_order(sc, ctx)
    if "native_limit" in r:
        return drive_gateway_limit(sc, ctx)
    if r.get("then") == "wait_usage_sync":
        return drive_attribution(sc, ctx)
    if "then" in r:  # every other admin-setup kind has its driver above
        return None, {"reason": "needs a scenario-specific driver (admin setup or fault injection)"}
    tok = ctx.tokens.get(sc.user, sc.idp)
    headers = bad_token(r["token"], tok) if "token" in r else {"Authorization": f"Bearer {tok}"}
    route = r["route"]

    def once():
        if route == "models":
            return http_call(ctx.base, "/inference/v1/models", "GET", headers, None)
        path = "/inference/v1/chat/completions" if route == "chat" else "/inference/v1/responses"
        return http_call(ctx.base, path, "POST", headers, json.dumps(body_for(sc)).encode())

    status, raw = once()
    body = raw[:400].decode(errors="replace")
    if sc.expect["status"] == 200 and responses_unserved(sc, status, body):
        return "n/a", {"reason": NA_RESPONSES, "status": status, "body": body}
    ok, seen = status == sc.expect["status"], {}
    if ok and sc.expect.get("has_text_or_tool"):
        said, called = reply_of(raw.decode(errors="replace"))
        # The gateway is under test, not the model's obedience: with a tool offered, any answer or tool call works
        # (r135: a made-up time instead of PONG). Without one the answer must say PONG, the sign the prompt reached
        # the model intact. A model that once answers otherwise (r138: "PING"), or with tools offered answers only in
        # its reasoning with no content and no tool call (r152: Kimi put " PONG" in reasoning), is asked once more,
        # visibly (retried), and the scenario fails if it misses again. The gateway's own answer was a 200 both times.
        tools = bool(r.get("tools"))

        def answered(said, called):
            return called or bool(said.strip()) if tools else "PONG" in said.upper()
        ok = answered(said, called)
        seen = {"answer": said[:200], "tool_called": called, "tools_offered": tools}
        if not ok and status == 200:  # any 200 the judge refused, empty answers included, is asked once more
            status2, raw2 = once()
            said2, called2 = reply_of(raw2.decode(errors="replace"))
            ok = status2 == 200 and answered(said2, called2)
            seen.update(retried=True, first_answer=said[:200], answer=said2[:200], tool_called=called2, retry_status=status2,
                        retry_body=raw2[:400].decode(errors="replace"))
    return ok, {"status": status, "body": body, **seen}


def reply_of(raw: str) -> tuple[str, bool]:
    """(the model's answer text, whether it called a tool) from a reply body, read by structure, never by substring
    (a Responses body always echoes "parallel_tool_calls"; r135 /code-review).

    A whole JSON reply: chat choices[].message (content, tool_calls), or a Responses body's output items (message
    output_text parts, function_call items; reasoning is not the answer). Anything else is read as a server-sent
    event stream, wherever it starts (comments, id: and retry: lines are skipped): chat chunks' delta content and
    tool_calls, and Responses output_text deltas and function_call items. A stream may split a word across chunks
    (r133: a PONG in two), so the pieces are joined first."""
    texts, called = [], False

    def take(obj):
        nonlocal called
        if not isinstance(obj, dict):
            return
        for ch in obj.get("choices") if isinstance(obj.get("choices"), list) else []:
            if not isinstance(ch, dict):
                continue
            for part in (ch.get("message"), ch.get("delta")):
                if isinstance(part, dict):
                    content = part.get("content")
                    if isinstance(content, str):
                        texts.append(content)
                    elif isinstance(content, list):  # content as a list of parts: text parts only, never reasoning
                        texts.extend(c["text"] for c in content if isinstance(c, dict) and c.get("type", "text") in
                                     ("text", "output_text") and isinstance(c.get("text"), str))
                    called = called or bool(part.get("tool_calls"))
        for item in obj.get("output") if isinstance(obj.get("output"), list) else []:
            if not isinstance(item, dict):
                continue
            called = called or item.get("type") == "function_call"
            if item.get("type") == "message" and isinstance(item.get("content"), list):
                texts.extend(p.get("text") for p in item["content"]
                             if isinstance(p, dict) and p.get("type") == "output_text" and isinstance(p.get("text"), str))
        kind = str(obj.get("type", ""))
        if kind.endswith("output_text.delta") and isinstance(obj.get("delta"), str):
            texts.append(obj["delta"])
        if kind == "response.output_item.added" and isinstance(obj.get("item"), dict):
            called = called or obj["item"].get("type") == "function_call"

    try:
        take(json.loads(raw))
    except (ValueError, RecursionError):
        for line in raw.splitlines():
            if line.startswith("data:") and line[5:].strip() != "[DONE]":
                with contextlib.suppress(ValueError, RecursionError):
                    take(json.loads(line[5:]))
    return "".join(texts), called


ATTACK_PATHS = {"encoded_path": "/inference/v1/chat%2Fcompletions", "double_encoded_path": "/inference/v1/chat%252Fcompletions",
                "dot_segments": "/inference/v1/x/../chat/completions", "unknown_route": "/inference/v1/embeddings",
                "invoke_path": "/model/ow/kimi-k3/invoke"}


SPOOFS = ("spoof_openai_project", "spoof_x_ocgw_user")


def drive_attack(sc, ctx):
    """F8.*: hostile or malformed requests. Status must match; allowed spoofing cases must not change attribution."""
    case, tok = sc.request["attack"], ctx.tokens.get(sc.user, sc.idp)
    path = ATTACK_PATHS.get(case, "/inference/v1/chat/completions" if sc.request["route"] == "chat" else "/inference/v1/responses")
    # The path attacks all aim at chat/completions, so they send a chat body whatever the scenario's route.
    good = json.dumps(body_for(S.Scenario(**{**sc.__dict__, "request": {**sc.request, "route": "chat"}})) if case in ATTACK_PATHS else body_for(sc))
    data = {"not_json": b"{not json", "json_array": b"[1,2]", "nan": good.replace("1024", "NaN").encode(),
            "infinity": good.replace("1024", "Infinity").encode(), "1e999": good.replace("1024", "1e999").encode(),
            "deep_nesting": ("[" * 5000 + "]" * 5000).encode(), "model_int": good.replace('"ow/kimi-k3"', "5").encode(),
            "model_null": good.replace('"ow/kimi-k3"', "null").encode(), "model_list": good.replace('"ow/kimi-k3"', '["ow/kimi-k3"]').encode(),
            "dup_model_key": ('{"model": "ow/deepseek-v3.2", ' + good[1:]).encode(), "huge_body_6mb": b'{"x": "' + b"a" * 6_000_000 + b'"}',
            }.get(case, good.encode())
    headers = {"Authorization": f"Bearer {tok}"}
    if case == "spoof_openai_project":
        headers["OpenAI-Project"] = "proj_someone_else"
    if case == "spoof_x_ocgw_user":
        headers["x-ocgw-user"] = "mallory"
    method = "GET" if case == "get_on_chat" else "POST"
    day, attr = time.strftime("DAY#%Y-%m-%d", time.gmtime()), f"m|{sc.request['model']}|req"

    def counted(pk):
        """(requests counted for this model today, the row exists). The interceptor ADDs this itself as it answers."""
        it = _ddb(ctx).get_item(TableName=ctx.table, Key={"pk": {"S": pk}, "sk": {"S": day}}, ConsistentRead=True).get("Item") or {}
        return float(it.get(attr, {}).get("N", 0)), bool(it)

    user = ctx.ledger_user(sc.user, sc.idp)
    before = counted(f"USER#{user}")[0] if case in SPOOFS else 0
    status, raw = http_call(ctx.base, path, method, headers, None if method == "GET" else data)
    body = raw[:300].decode(errors="replace")
    want = sc.expect["status"]
    if want == 200 and responses_unserved(sc, status, body):  # a case that needs a served route cannot run on this one
        return "n/a", {"reason": NA_RESPONSES, "status": status, "body": body}
    # 413 may come back as 400 from the front end; a dot-segment path is refused by the front end with 403.
    ok = status == want or (want == 413 and status in (400, 413)) or (case == "dot_segments" and status in (403, 404))
    detail = {"status": status, "body": body}
    if ok and case in SPOOFS:
        # The status alone would pass even if the spoofed header had been honoured. That the header never reaches the
        # model, and that the caller's own project is injected instead, is proved exactly by
        # test_client_cannot_set_project (tests/unit/test_interceptor.py); here, live, the name in the header must
        # never have become a user of this gateway - x-ocgw-user: "mallory" would otherwise be refused as unmapped or
        # counted on a row of its own. The caller's own counter is reported but not required: count_request writes in
        # the background and Lambda can freeze the container until the next invocation, which would make this race.
        after, _ = counted(f"USER#{user}")
        spoofed, spoofed_row = counted("USER#mallory")
        detail |= {"counted_on_caller": after - before, "spoofed_row": spoofed_row, "spoofed_requests": spoofed}
        ok = case != "spoof_x_ocgw_user" or (not spoofed_row and spoofed == 0)
    return ok, detail


def drive_xuser(sc, ctx):
    """F8 cross-user cases: alice stores a response; bob tries to continue it (or sends a method override)."""
    case = sc.request["xuser"]
    path = "/inference/v1/chat/completions" if sc.request["route"] == "chat" else "/inference/v1/responses"
    auth = {"Authorization": f"Bearer {ctx.tokens.get(sc.user, sc.idp)}"}
    if case == "x_http_method_override_delete":
        # The override must not reach another handler: route_of() routes on the path alone and there is no DELETE
        # route, so the only outcomes are the normal answer or a refusal. AgentCore itself drops the body of a request
        # carrying DELETE or GET in this header (probed r126), so that refusal is the interceptor's own "no model" 400
        # and nothing was acted on. Either way the next ordinary call must still be answered.
        st, raw = http_call(ctx.base, path, "POST", {**auth, "X-HTTP-Method-Override": "DELETE"},
                            json.dumps(body_for(sc)).encode())
        after, _ = http_call(ctx.base, path, "POST", auth, json.dumps(body_for(sc)).encode())
        msg = raw[:300].decode(errors="replace")
        ok = (st in sc.expect["status_in"] and after == 200
              and (st != 400 or "model must be a non-empty string" in msg))  # a 400 for any other reason is not this
        return ok, {"status": st, "after": after, "body": msg}
    st, raw = http_call(ctx.base, path, "POST", {"Authorization": f"Bearer {ctx.tokens.get('alice', sc.idp)}"},
                        json.dumps({**body_for(sc), "store": True}).encode())
    rid = json.loads(raw or b"{}").get("id") if st == 200 else None
    if not rid:
        msg = raw[:300].decode(errors="replace")
        if responses_unserved(sc, st, msg):
            return "n/a", {"reason": f"{NA_RESPONSES}, so there is no stored response for another user to reach",
                           "status": st, "body": msg}
        return False, {"reason": "alice could not create a stored response", "status": st}
    b = {**body_for(sc), "previous_response_id": rid}
    st2, raw2 = http_call(ctx.base, path, "POST", auth, json.dumps(b).encode())
    return st2 != sc.expect["status_not"], {"status": st2, "body": raw2[:300].decode(errors="replace")}


def ledger_delete(ctx, user, sk):
    import boto3  # noqa: PLC0415
    boto3.client("dynamodb", region_name=ctx.region).delete_item(TableName=ctx.table, Key={"pk": {"S": f"USER#{user}"}, "sk": {"S": sk}})


def _ddb(ctx):
    import boto3  # noqa: PLC0415
    return boto3.client("dynamodb", region_name=ctx.region)


@contextlib.contextmanager
def ledger_patch(ctx, user, sk, **attrs):
    """Set some attributes of one ledger item for the length of a scenario, then put back exactly those attributes
    (their old values, or remove them if they were absent). The rest of the item (token_groups, the seeded group,
    overrides, boosts, counters) is never touched, so a run leaves the live stack as it found it."""
    key = {"pk": {"S": f"USER#{user}"}, "sk": {"S": sk}}
    old = _ddb(ctx).get_item(TableName=ctx.table, Key=key, ConsistentRead=True).get("Item") or {}
    names = {f"#a{i}": k for i, k in enumerate(attrs)}
    _ddb(ctx).update_item(TableName=ctx.table, Key=key, ExpressionAttributeNames=names,
                          UpdateExpression="SET " + ", ".join(f"#a{i} = :a{i}" for i in range(len(attrs))),
                          ExpressionAttributeValues={f":a{i}": {"N": str(v)} for i, v in enumerate(attrs.values())})
    try:
        yield
    finally:
        keep = {n: a for n, a in names.items() if a in old}
        gone = [n for n, a in names.items() if a not in old]
        expr = " ".join(x for x in ("SET " + ", ".join(f"{n} = :o{n[2:]}" for n in keep) if keep else "",
                                    "REMOVE " + ", ".join(gone) if gone else "") if x)
        _ddb(ctx).update_item(TableName=ctx.table, Key=key, ExpressionAttributeNames=names, UpdateExpression=expr,
                              **({"ExpressionAttributeValues": {f":o{n[2:]}": old[a] for n, a in keep.items()}} if keep else {}))
        if not old:
            _drop_if_bare(ctx, key)


def _drop_if_bare(ctx, key, allowed=()):
    """Delete an item this run created, once nothing but its key (and `allowed`, at zero) is left on it. If the gateway
    wrote something else to it meanwhile (a request counter, token_groups), it is real state now and stays."""
    it = _ddb(ctx).get_item(TableName=ctx.table, Key=key, ConsistentRead=True).get("Item") or {}
    if it and set(it) - {"pk", "sk"} <= set(allowed) and all(abs(float(it[a]["N"])) < 1e-9 for a in allowed if a in it):
        _ddb(ctx).delete_item(TableName=ctx.table, Key=key)


def window_now(ctx, user, sk):
    """window_state's spend alone."""
    return window_state(ctx, user, sk)[0]


def window_state(ctx, user, sk):
    """The window's spend as the interceptor sums it (policy.window_read). A record the gateway cannot read is refused
    once the scenario puts a cap on it, so the scenario stops here and says so instead of failing for a reason it hides."""
    it = _ddb(ctx).get_item(TableName=ctx.table, Key={"pk": {"S": f"USER#{user}"}, "sk": {"S": sk}},
                            ConsistentRead=True).get("Item") or {}
    spent, ok = policy.window_read(it)
    if not ok:
        raise RuntimeError(f"USER#{user} {sk} holds a spend record the gateway cannot read; fix it before this scenario")
    return spent, bool(it)


@contextlib.contextmanager
def seeded_spend(ctx, user, sk, target, base=None, existed=None):
    """Make the window's spend (model_spend_usd + spend_usd, as the interceptor sums it) equal target, by ADDing the
    difference to spend_usd and subtracting it again afterwards. usage_sync SETs model_spend_usd from CloudWatch every
    5 minutes and never touches spend_usd, so the seed survives a sync during the call."""
    key = {"pk": {"S": f"USER#{user}"}, "sk": {"S": sk}}
    if existed is None:
        existed = bool(_ddb(ctx).get_item(TableName=ctx.table, Key=key, ConsistentRead=True).get("Item"))
    # base, existed: what the caller already read (drive_budget sets its cap from it), so both use one reading.
    delta = target - (base if base is not None else window_now(ctx, user, sk))

    def add(x):
        _ddb(ctx).update_item(TableName=ctx.table, Key=key, UpdateExpression="ADD spend_usd :d",
                              ExpressionAttributeValues={":d": {"N": f"{x:.8f}"}})
    add(delta)
    try:
        yield
    finally:
        add(-delta)
        if not existed:  # the ADD created the window item (with no ttl): remove it again unless the call used it
            _drop_if_bare(ctx, key, allowed=("spend_usd",))


def _rate_rule(ctx):
    import boto3  # noqa: PLC0415
    c = boto3.client("bedrock-agentcore-control", region_name=ctx.region)
    gw = ctx.outs["gateway_id"]
    # The gateway allows one limit per set of dimension keys; Terraform's is the one on the user claim. Every page is
    # read, and each limit is fetched in full (summaries need not carry a description or the keys).
    w = _agentcore_wait()
    ids = [x["rateLimitId"] for x in w.list_all(c.list_gateway_rate_limits, ("rateLimits", "items"), gatewayIdentifier=gw)]
    # Terraform's limit is the one on a single user-claim key that carries the "*" default (whatever var.name is);
    # a limit deleted between List and Get is skipped.
    for rid in ids:
        d = w.get_or_none(c.get_gateway_rate_limit, gatewayIdentifier=gw, rateLimitId=rid)
        if d is None:
            continue
        keys = d.get("dimensionKeys") or []
        if len(keys) == 1 and keys[0].startswith("$.context.jwt.") and any(
                (e.get("dimensions") or {}).get(keys[0]) == "*" for e in d.get("entries") or []):
            return c, gw, rid, d
    raise RuntimeError("no Terraform rate limit on the gateway")


def _identity_map(ctx):
    """IdP id -> ledger user, from the ledger's CONFIG/DIRECTORY item (Terraform writes it; there is no output)."""
    import boto3  # noqa: PLC0415
    it = boto3.client("dynamodb", region_name=ctx.region).get_item(
        TableName=ctx.outs["usage_table"], Key={"pk": {"S": "CONFIG"}, "sk": {"S": "DIRECTORY"}}).get("Item")
    return json.loads(it["identity_map"]["S"]) if it else {}


def _agentcore_wait():
    helpers = str(Path(__file__).resolve().parents[2] / "terraform" / "helpers")
    if helpers not in sys.path:
        sys.path.insert(0, helpers)
    import agentcore_wait  # noqa: PLC0415 - the helpers' own wait: stale ACTIVE, *_FAILED and a resource that is gone
    return agentcore_wait


def _rate_update(c, gw, rid, desc, entries):
    before = c.get_gateway_rate_limit(gatewayIdentifier=gw, rateLimitId=rid)
    c.update_gateway_rate_limit(gatewayIdentifier=gw, rateLimitId=rid, description=desc, entries=entries)
    w = _agentcore_wait()
    w.wait_active(lambda: w.get_or_none(c.get_gateway_rate_limit, gatewayIdentifier=gw, rateLimitId=rid),
                  f"rate limit {rid}", before=before, delay=2)
    # AgentCore documents up to 30 s for a rate-limit change to reach the data plane, and limits fail open until then
    # (devguide: gateway-rate-limits.html, "Propagation time <= 30 seconds"). 15 s here made F6.gateway_rpm pass or
    # fail on timing alone (r126: one run 429-free, the next enforced), so wait the documented bound plus margin.
    time.sleep(35)


def _jwt_claims(token):
    """A JWT's claims, unverified, as the client reads them (ocgw._claims_or_empty: {} for anything that is not a
    three-part JWT with a JSON-object payload); the runner reads its own token to see which value a limit keys on."""
    return _ocgw_module()._claims_or_empty({"t": token}, "t")


def drive_gateway_limit(sc, ctx):
    """F6.gateway_*: the gateway's own per-user rate limit (not the interceptor's rpm). With a limit of None, the
    user's standing requests limit as deployed; otherwise lower the user's requests or tokens per minute on the live rule, lift the interceptor's rpm for the user out of the way, fire a burst in one
    minute and expect some 429s, then put the rule and the rpm back exactly as they were."""
    import copy  # noqa: PLC0415
    (target, limit), = sc.request["native_limit"].items()
    user = ctx.ledger_user(sc.user, sc.idp)
    c, gw, rid, rule = _rate_rule(ctx)
    entries = copy.deepcopy(rule["entries"])
    # The rule's own dimension key (user_claim) and the value the gateway sees for this user: the ledger name, or the
    # IdP id identity_map maps to it.
    dk = rule["dimensionKeys"][0]
    ids = {user} | {sub for sub, name in _identity_map(ctx).items() if name == user}
    # The entry for the value the gateway keys on (dk) in this user's token: the ledger name when the claim carries it
    # (Cognito's username), else the IdP id identity_map maps to the user.
    claims = _jwt_claims(ctx.tokens.get(sc.user, sc.idp))
    sent = claims.get(dk.rsplit(".", 1)[-1]) if dk.startswith("$.context.jwt.") else None
    mine = next((e for e in entries if e["dimensions"].get(dk) == sent), None) if isinstance(sent, str) and sent in ids else None
    mine = mine or next((e for e in entries if e["dimensions"].get(dk) in ids), None)
    if mine is None:
        return False, {"reason": f"no entry for {sorted(ids)} under {dk} in the gateway rule"}
    standing = limit is None  # the limit Terraform deployed (rpm_overrides), tested as is: no edit, no restore
    if standing:
        rates = [r for r in mine.get("requests") or [] if r.get("period") == "minute"]  # the burst is one minute's
        if not rates:
            return False, {"reason": f"no standing requests limit for {sorted(ids)} in the gateway rule"}
        if sc.request["burst"] <= min(r["rate"] for r in rates):
            return False, {"reason": f"burst {sc.request['burst']} is not over the standing limit {rates}"}
    else:
        mine["requests" if target == "gateway_rpm" else "tokens"] = [{"rate": float(limit), "period": "minute"}]
    # The 429 that answers this scenario names its own metric: a tokens 429 left over from an F6.gateway_tpm run must
    # not pass a requests scenario (r186: 0798 passed on a tokens 429 while its 8 calls a minute all got 200).
    metric = '"metric":"requests"' if target == "gateway_rpm" else '"metric":"tokens"'

    def its_429(st, b):
        return st == 429 and metric in b.replace(" ", "")
    codes, bodies = [], []
    try:  # the restore runs even if the update's wait or the burst fails
        if not standing:
            _rate_update(c, gw, rid, rule["description"], entries)
        # Lift the interceptor's rpm and budget caps for the user, so a 429 can only be the gateway's.
        # r128: one run sent 7 calls at a 1/minute limit 35 s after the change and got 7 x 200; the next scenario, same
        # wait, was enforced. The data plane can take longer than the documented 30 s (r178 and r182: the first gateway
        # scenario of a full run saw 14 x 200 over two minutes, then passed on rerun), so a burst with no 429 is repeated
        # in each of the next two minutes. Every burst is reported; a limit that never bites in three minutes still fails.
        with ledger_patch(ctx, user, "BUDGET", rpm=1000, budget_usd=100000, daily_usd=100000, hourly_usd=100000):
            for _attempt in range(3 if sc.expect["some_429"] else 1):  # a scenario expecting no 429 needs no later minute
                time.sleep(60 - time.gmtime().tm_sec + 1)  # a fresh minute for the gateway's window
                burst = []
                for _ in range(sc.request["burst"]):
                    st, raw = http_call(ctx.base, "/inference/v1/chat/completions", "POST", {"Authorization": f"Bearer {ctx.tokens.get(sc.user, sc.idp)}"},
                                        json.dumps(body_for(sc)).encode())
                    burst.append(st)
                    bodies.append(raw[:160].decode(errors="replace"))
                codes.append(burst)
                if any(its_429(st, b) for st, b in zip(burst, bodies[-len(burst):])):
                    break  # the gateway's 429 for this metric; the interceptor's rpm or the other metric is not the answer
    finally:
        if not standing:
            _rate_update(c, gw, rid, rule["description"], rule["entries"])
    flat = [st for burst in codes for st in burst]
    gateway_429 = [b for st, b in zip(flat, bodies) if its_429(st, b)]
    return bool(gateway_429) == sc.expect["some_429"], {"codes": codes, "first_429": gateway_429[:1], "retried": len(codes) > 1}


def drive_gateway_order(sc, ctx):
    """F6.gateway_order: set the user's gateway requests limit to 0 (blocks all of their traffic), wait for it to
    reach the data plane, send one call for a model the gateway does not serve, then put the rule back. A gateway 429
    on the requests metric means the rate limit runs before the REQUEST interceptor; the interceptor's 404 means it
    runs after. Anything else fails the scenario."""
    import copy  # noqa: PLC0415
    user = ctx.ledger_user(sc.user, sc.idp)
    c, gw, rid, rule = _rate_rule(ctx)
    entries = copy.deepcopy(rule["entries"])
    dk = rule["dimensionKeys"][0]
    ids = {user} | {sub for sub, name in _identity_map(ctx).items() if name == user}
    mine = next((e for e in entries if e["dimensions"].get(dk) in ids), None)
    if mine is None:
        return False, {"reason": f"no entry for {sorted(ids)} under {dk} in the gateway rule"}
    mine["requests"] = [{"rate": 0.0, "period": "minute"}]
    tries = []
    try:
        _rate_update(c, gw, rid, rule["description"], entries)
        for _attempt in range(3):  # limits can take longer than the documented 30 s to bite (see drive_gateway_limit)
            st, raw = http_call(ctx.base, "/inference/v1/chat/completions", "POST",
                                {"Authorization": f"Bearer {ctx.tokens.get(sc.user, sc.idp)}"}, json.dumps(body_for(sc)).encode())
            b = raw[:200].decode(errors="replace")
            tries.append((st, b))
            if st == 429 and '"metric":"requests"' in b.replace(" ", ""):
                return True, {"order": "rate_limit_before_interceptor", "tries": tries}
            if st != 404 or "not served by this gateway" not in b:
                return False, {"reason": "neither the gateway 429 nor the interceptor's 404", "tries": tries}
            time.sleep(20)  # a 404 may come before the 0 limit reached every gateway host: try again
        return True, {"order": "interceptor_before_rate_limit", "tries": tries}
    finally:
        _rate_update(c, gw, rid, rule["description"], rule["entries"])


def _rules(c, gw):
    return _agentcore_wait().list_all(c.list_gateway_rules, ("gatewayRules", "rules", "items"), gatewayIdentifier=gw)


def _rule_update(c, gw, rid, rule):
    before = c.get_gateway_rule(gatewayIdentifier=gw, ruleId=rid)
    c.update_gateway_rule(gatewayIdentifier=gw, ruleId=rid, priority=rule["priority"], conditions=rule["conditions"],
                          actions=rule["actions"], description=rule["description"])
    w = _agentcore_wait()
    w.wait_active(lambda: w.get_or_none(c.get_gateway_rule, gatewayIdentifier=gw, ruleId=rid), f"rule {rid}",
                  before=before, delay=2)
    time.sleep(20)


def drive_fallback(sc, ctx):
    """F7.*: make the primary model fail on the live rule, call it, and expect the answer from the fallback hop; then
    put the rule back. first_byte_timeout: the primary's first-byte timeout becomes 50 ms (the minimum), so the hop
    answers. model_missing: the primary names a Mantle model that does not exist; that is a 404, which is not a
    fallback trigger, so the caller gets the 404. throttle, 5xx and unreachable cannot be forced from outside."""
    import boto3, copy  # noqa: PLC0415, E401
    fault = sc.request["fault"]
    if fault not in ("first_byte_timeout", "model_missing"):
        return None, {"reason": f"{fault}: Mantle cannot be made to throttle, fail or be unreachable on demand; covered by the rule's triggerOn and unit tests"}
    c = boto3.client("bedrock-agentcore-control", region_name=ctx.region)
    gw = ctx.outs["gateway_id"]
    alias = sc.request["model"].split("/", 1)[1]  # the scenario's primary, e.g. glm-5.3: its own rule, no other
    want_desc = f"{ctx.outs.get('name', 'ocgw')} fallback {alias}"
    rid = next((x["ruleId"] for x in _rules(c, gw) if x.get("description", "") == want_desc or x.get("description", "").startswith(want_desc + " #")), None)
    if rid is None:
        return False, {"reason": f"no gateway rule '{want_desc}'"}
    rule = c.get_gateway_rule(gatewayIdentifier=gw, ruleId=rid)
    bad = copy.deepcopy(rule)
    route = bad["actions"][0]["routeToTarget"]["staticRoute"]
    if fault == "first_byte_timeout":
        route["fallback"]["firstByteTimeoutMilliseconds"] = 50  # the API minimum; no model answers its first byte that fast
    else:
        route["model"] = "zai.ocgw-e2e-missing-model"
    want = rule["actions"][0]["routeToTarget"]["staticRoute"]["fallback"]["targetFallback"][0]["model"]  # first hop
    try:  # the restore runs even if the update's wait or the call fails
        _rule_update(c, gw, rid, bad)
        st, raw = http_call(ctx.base, "/inference/v1/chat/completions", "POST", {"Authorization": f"Bearer {ctx.tokens.get(sc.user, sc.idp)}"},
                            json.dumps(body_for(sc)).encode())
    finally:
        _rule_update(c, gw, rid, rule)
    text = raw.decode(errors="replace")
    served = json.loads(text).get("model") if st == 200 else None
    return st == sc.expect["status"] and (st != 200 or served == want), {"status": st, "served_by": served, "want": want, "body": text[:200]}


def drive_attribution(sc, ctx, wait_s=1500):
    """F10.*: one call as the user, then wait for usage_sync (every 5 min, plus Mantle's CloudWatch delay) until the
    user's, the model's and the group's DAY rollups all show more model input tokens than before the call."""
    user = ctx.ledger_user(sc.user, sc.idp)
    day = time.strftime("DAY#%Y-%m-%d", time.gmtime())
    keys = {"user": f"USER#{user}", "model": f"MODEL#{sc.request['model']}", "group": f"GROUP#{S.USERS[sc.user][0]}"}

    def tokens():
        out = {}
        for k, pk in keys.items():
            it = _ddb(ctx).get_item(TableName=ctx.table, Key={"pk": {"S": pk}, "sk": {"S": day}}, ConsistentRead=True).get("Item") or {}
            out[k] = float(it.get("model_input_tokens", {}).get("N", 0))
        return out
    before = tokens()
    st, raw = http_call(ctx.base, "/inference/v1/chat/completions", "POST", {"Authorization": f"Bearer {ctx.tokens.get(sc.user, sc.idp)}"},
                        json.dumps(body_for(sc)).encode())
    if st != 200:
        return False, {"status": st, "body": raw[:300].decode(errors="replace")}
    t0 = time.time()
    while time.time() - t0 < wait_s:
        now = tokens()
        if all(now[k] > before[k] for k in keys):
            return True, {"before": before, "after": now, "waited_s": round(time.time() - t0)}
        time.sleep(30)
    return False, {"before": before, "after": tokens(), "waited_s": wait_s}


def drive_budget(sc, ctx):
    """F5.*: set a cap on the user's BUDGET item and seed spend in the window, call, then restore."""
    (window, cap), = sc.request["set_cap"].items()
    kind = policy.PERIOD_KIND[window]
    key = policy.USER_KEYS[window]
    user = ctx.ledger_user(sc.user, sc.idp)
    # The window already holds real spend (model calls, searches). Seeding below it would need a negative spend_usd,
    # which the gateway refuses as an unreadable record (policy.window_read, r141), so the cap and the seeded
    # spend are both set on top of what the window holds: the scenario's distance to the cap is the same.
    case = sc.request["spent"]

    def attempt():
        sk = policy.window_sk(kind, time.gmtime())  # per attempt: a retry after the hour turns seeds the new window
        base, existed = window_state(ctx, user, sk)  # one read for the cap, the seed and the cleanup
        # "under" keeps the whole cap as headroom (spend = what the window holds), so usage_sync landing the window's
        # earlier model spend during the call does not push it over (r142: $0.37 became $0.40 against $0.39).
        spent = base + {"under": 0.0, "at": cap, "over": cap + 1}[case]
        with ledger_patch(ctx, user, "BUDGET", **{key: round(base + cap, 6)}), seeded_spend(ctx, user, sk, spent, base, existed):
            status, raw = http_call(ctx.base, "/inference/v1/chat/completions", "POST",
                                    {"Authorization": f"Bearer {ctx.tokens.get(sc.user, sc.idp)}"}, json.dumps(body_for(sc)).encode())
        return status == sc.expect["status"] and (sc.expect.get("code") is None or sc.expect["code"].encode() in raw), status, raw

    ok, status, raw = attempt()
    detail = {"status": status, "body": raw[:300].decode(errors="replace")}
    if not ok and case == "under" and b"budget_exceeded" in raw:  # the sync moved the window mid-call: once more, visibly
        ok, status, raw = attempt()
        detail.update(retried=True, retry_status=status, retry_body=raw[:300].decode(errors="replace"))
    return ok, detail


def drive_rpm(sc, ctx):
    """F6.rpm.*: set the user's rpm, fire `burst` calls in one minute, count 200s and 429s."""
    rpm, burst = sc.request["set_rpm"], sc.request["burst"]
    user = ctx.ledger_user(sc.user, sc.idp)
    # Start at a fresh minute: the burst stays in one interceptor RPM# bucket, and the gateway's own per-minute window
    # (60 a minute by default) holds none of the previous scenario's calls. Slow (one scenario a minute), but exact.
    time.sleep(60 - time.time() % 60 + 0.5)
    ledger_delete(ctx, user, time.strftime("RPM#%Y-%m-%dT%H:%M", time.gmtime()))  # this minute's counter (10-minute ttl)
    codes = []
    with ledger_patch(ctx, user, "BUDGET", rpm=rpm):
        for _ in range(burst):
            st, _raw = http_call(ctx.base, "/inference/v1/chat/completions", "POST", {"Authorization": f"Bearer {ctx.tokens.get(sc.user, sc.idp)}"},
                                 json.dumps({**body_for(sc), "max_tokens": 16}).encode())
            codes.append(st)
    ok = codes.count(200) == sc.expect["allowed"] and codes.count(429) == sc.expect["rejected_429"]
    return ok, {"codes": codes}


def drive_mcp(sc, ctx):
    """F9.*: JSON-RPC tools/call on the web search MCP gateway as the user."""
    q = sc.request["query"]
    tok = ctx.tokens.get(sc.user, sc.idp)  # nogroup: a Cognito test user with no group (cognito_extra_users)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "web___WebSearch", "arguments": {"query": q, "maxResults": 3}}}
    status, raw = http_call(ctx.websearch, "", "POST", {"Authorization": f"Bearer {tok}", "Accept": "application/json, text/event-stream"},
                            json.dumps(body).encode())
    text = raw.decode(errors="replace")
    got_ok = status == 200 and '"error"' not in text and '"isError": true' not in text and '"isError":true' not in text
    return got_ok == sc.expect["ok"], {"status": status, "body": text[:300]}


# OPENCODE_BIN, else whatever "opencode" resolves to on PATH. "" when opencode is not installed: the scenarios that
# need it are then reported as not driven, with that reason, rather than one traceback per scenario.
OPENCODE = os.environ.get("OPENCODE_BIN") or shutil.which("opencode") or ""


PYTHON = sys.executable  # the interpreter running this file; children run it too


def _py_env(env):
    """The child's environment with this interpreter's directory first on PATH, so argv[0] "python3" resolves to it."""
    return {**env, "PATH": os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")}


def py_run(args, env, **kw):
    """Run a script of this repo with this interpreter: argv[0] is the literal "python3", executable= the interpreter."""
    return subprocess.run(["python3", "-B", *args], executable=PYTHON, env=_py_env(env), **kw)  # nosec B603


def py_popen(args, env, **kw):
    return subprocess.Popen(["python3", "-B", *args], executable=PYTHON, env=_py_env(env), **kw)  # nosec B603


def oc_run(args, env, **kw):
    """Run opencode: argv[0] is the literal name, executable= the binary resolved above (OPENCODE_BIN or PATH)."""
    return subprocess.run(["opencode", *args], executable=OPENCODE, env=env, **kw)  # nosec B603
CLIENT = HERE.parents[1] / "client" / "ocgw.py"
_HOMES: dict = {}


def _restore_at_exit(path):
    """Put `path` back as it is now when the run ends: its bytes, or no file. main() turns SIGTERM into a normal exit,
    so a timed-out or cancelled run restores too (SIGKILL cannot be caught)."""
    old = path.read_bytes() if path.exists() else None
    atexit.register(lambda: path.write_bytes(old) if old is not None else path.unlink(missing_ok=True))


def user_home(ctx, user: str, idp: str, tag: str = "") -> tuple[dict, subprocess.Popen]:
    """A private HOME for one test user, signed in as a developer would be: ~/.config/ocgw/config.json (the stack's
    ocgw_config), tokens.json from a Cognito sign-in, `ocgw opencode-config`, and `ocgw proxy` running on its own port.
    Built once per user and kept for the run; tests/e2e/out is git-ignored (the tokens live there)."""
    if (user, idp, tag) in _HOMES:
        return _HOMES[(user, idp, tag)]
    with socket.socket() as probe:  # a port no other live proxy holds (len(_HOMES) shrinks when client homes are popped)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    cfg = {**ocgw_config_for(ctx, user, idp), "proxy_port": port}
    if idp != "cognito":
        # The user's own signed-in home, not a copy: two copies of one rotating refresh token would each spend it,
        # and the IdP would revoke the pair. ocgw serialises refreshes in that home with its file lock.
        home = signed_home(user, idp, tag)
        cfg_dir = home / ".config" / "ocgw"
        cfg_file = cfg_dir / "config.json"
        # The run's proxy port goes into the operator's own home for the run only: config.json, and the opencode.json
        # that `ocgw opencode-config` writes below, are put back as they were (a file that was not there is removed).
        _restore_at_exit(cfg_file)
        _restore_at_exit(home / ".config" / "opencode" / "opencode.json")
        cfg_file.write_text(json.dumps(cfg))
    else:
        home = OUT / "homes" / f"{user}-{idp}{'-' + tag if tag else ''}"
        cfg_dir = home / ".config" / "ocgw"
        cfg_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        (cfg_dir / "config.json").write_text(json.dumps(cfg))
        r = cognito_sign_in(ctx.outs, ctx.region, user)
        tok = {"id_token": r["IdToken"], "access_token": r["AccessToken"], "refresh_token": r.get("RefreshToken", ""),
               "expires_in": r["ExpiresIn"], "token_type": "Bearer", "obtained_at": int(time.time())}
        fd = os.open(cfg_dir / "tokens.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(tok))
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", ""), "XDG_CONFIG_HOME": str(home / ".config"),
           "XDG_DATA_HOME": str(home / ".local" / "share"), "XDG_CACHE_HOME": str(home / ".cache"),
           "XDG_STATE_HOME": str(home / ".local" / "state")}
    py_run([str(CLIENT), "opencode-config"], env, check=True, capture_output=True, timeout=60)
    proc = py_popen([str(CLIENT), "proxy"], env, stdout=subprocess.DEVNULL,
                            stderr=open(home / "proxy.log", "w"))  # noqa: SIM115 - lives as long as the proxy
    atexit.register(proc.terminate)  # before the config restores (atexit runs last-in first), and on SIGTERM too
    for _ in range(50):
        with contextlib.suppress(OSError):
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/")
            break
        time.sleep(0.2)
    time.sleep(0.5)  # a proxy that lost the port exits at bind; give it the moment, then check it is ours that answered
    if proc.poll() is not None:  # it exited (a port clash, say): another proxy answering here would mislead every case
        raise RuntimeError(f"ocgw proxy for {user} exited: {(home / 'proxy.log').read_text()[-300:]}")
    env["OCGW_PROXY_PORT"] = str(port)  # for the drivers only; ocgw reads the port from config.json
    _HOMES[(user, idp, tag)] = (env, proc)
    return env, proc


def drive_opencode(sc, ctx):
    """F2.opencode.*: the real opencode CLI, headless (`opencode run`), through ocgw proxy, as the user."""
    if not OPENCODE:
        return None, {"reason": "opencode is not installed: put it on PATH or set OPENCODE_BIN (see the README)"}
    if "task" in sc.request:
        return drive_task(sc, ctx)
    env, _ = user_home(ctx, sc.user, sc.idp)

    def once():
        r = oc_run(["run", "-m", sc.request["model"], sc.request["prompt"]], env, cwd=env["HOME"],
                   capture_output=True, text=True, timeout=240)
        return r.returncode, "PONG" in r.stdout.upper(), (r.stdout + r.stderr)[-400:]

    rc, pong, out = once()
    detail = {"rc": rc, "out": out}
    # The same rule as the chat scenarios (testing.md): a session that ran cleanly (rc 0) but whose model answered
    # without PONG (r170: glm-5.3 said "No, I'm sorry, but I can't do that.") is asked once more, visibly. A model the
    # user may not use is never retried, and neither is a session that failed.
    if sc.expect["pong"] and rc == 0 and not pong:
        rc, pong, out2 = once()
        detail.update(retried=True, first_out=out, rc=rc, out=out2)
    return pong == sc.expect["pong"], detail


def _oc(env, work, model, prompt, cont=False):
    r = oc_run(["run", "--auto", "--dir", str(work), "-m", model, *(["--continue"] if cont else []), prompt], env,
               cwd=work, capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout + r.stderr


def drive_task(sc, ctx):
    """F2.task.*: real multi-step opencode sessions (tools: write, bash, edit; a continued session; a model switch)
    in an empty work folder inside the user's home. --auto approves the tool calls there.

    A session whose every step the gateway answered (rc 0) but whose result the model got wrong (r131: an edit that
    wrote GREETING = \\"...\\" with the backslashes) is run once more in a fresh folder: the gateway is under test,
    not the model's exactness. Both attempts are in the result; a failed step (rc != 0) is never retried."""
    attempts = []
    for _ in range(2):
        ok, ran, log = _task_once(sc, ctx)
        attempts.append(log)
        if ok is None or ok or not ran or not sc.expect["ok"]:  # retry only an expected success the model missed
            break
    if ok is None:
        return None, log
    return ok == sc.expect["ok"], {"attempts": attempts, "retried": len(attempts) > 1}


def _ocgw_module():
    """The client module itself (client/ocgw.py), for the recorder's use of its reply framing. Loaded under a private
    name, published only once whole, and called from the recorder's constructor (one thread), never from its handler
    threads. Importing it has no side effect on this process (ocgw's no-redirect opener is its own, not urllib's)."""
    import importlib.util  # noqa: PLC0415
    if "_ocgw_client" not in sys.modules:
        spec = importlib.util.spec_from_file_location("_ocgw_client", CLIENT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules["_ocgw_client"] = mod
    return sys.modules["_ocgw_client"]


class _ReplyRecorder:
    """A test-only hop between opencode and the user's ocgw proxy for the F2.task sessions, on 127.0.0.1 only: it
    forwards each POST (opencode's model calls) and keeps every reply in <work>-replies/, beside the session's folder
    so the model never sees it, so a session the model ended
    without its tool call (r156 to r164: one step, about 70 output tokens, no text and no tool part) leaves the raw
    stream to read. replies() summarises each one: where its output went and how it finished."""

    def __init__(self, port: int, out: Path):
        import http.server  # noqa: PLC0415 - only the F2.task driver needs a server
        import itertools  # noqa: PLC0415
        import threading  # noqa: PLC0415
        out.mkdir(parents=True, exist_ok=True)
        seq = itertools.count()
        relay = _ocgw_module().relay_reply  # loaded here, before any handler thread runs

        class Hop(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # chunked replies: a stream cut short upstream stays visibly unterminated

            def do_POST(self):  # noqa: N802 - the stdlib's handler name
                n = next(seq)
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))  # opencode sends a length
                (out / f"{n:02d}-request.json").write_bytes(body)
                fwd = {k: v for k, v in self.headers.items() if k.lower() in ("authorization", "content-type", "accept")}
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=300)
                try:
                    c.request("POST", self.path, body, fwd)
                    r = c.getresponse()
                except (OSError, http.client.HTTPException) as e:  # proxy down, reset or garbled: answer as a gateway would
                    (out / f"{n:02d}-status").write_text("502")
                    (out / f"{n:02d}-reply.sse").write_text(f"hop error: {e}")
                    self.send_error(502, f"recording hop: {e}")
                    self.close_connection = True
                    c.close()
                    return
                (out / f"{n:02d}-status").write_text(str(r.status))
                # One reply per connection, said in the reply (relay_reply sends Connection: close when it is set): a
                # keep-alive socket would hold this handler thread in a read with no timeout, and server_close() joins
                # every handler thread, so close() could wait forever.
                self.close_connection = True
                try:  # ocgw's own framing and hop-by-hop rules, so the hop never differs from the proxy behind it
                    with open(out / f"{n:02d}-reply.sse", "wb") as f:
                        outcome = relay(self, r, tee=f.write)
                    (out / f"{n:02d}-outcome").write_text(outcome)
                finally:
                    c.close()

            def log_message(self, *a):
                pass

        self.out, self.srv = out, http.server.ThreadingHTTPServer(("127.0.0.1", 0), Hop)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def replies(self) -> list[dict]:
        rows = []
        for f in sorted(self.out.glob("*-reply.sse")):
            kinds, finish, usage = set(), None, None
            text = f.read_text(errors="replace")
            for line in text.splitlines():
                if not line.startswith("data:") or line.strip() == "data: [DONE]":
                    continue
                with contextlib.suppress(ValueError):
                    ev = json.loads(line[5:])
                    if not isinstance(ev, dict):
                        continue
                    usage = ev.get("usage") if isinstance(ev.get("usage"), dict) else usage
                    for ch in ev.get("choices") if isinstance(ev.get("choices"), list) else []:
                        d = (ch.get("delta") if isinstance(ch, dict) else None) or {}
                        if isinstance(d, dict):
                            kinds |= {k for k in ("content", "tool_calls", "reasoning_content", "reasoning") if d.get(k)}
                        finish = (ch.get("finish_reason") if isinstance(ch, dict) else None) or finish
            st = f.with_name(f.name.replace("-reply.sse", "-status"))
            oc = f.with_name(f.name.replace("-reply.sse", "-outcome"))
            rows.append({"file": str(f), "status": st.read_text() if st.exists() else None, "finish": finish,
                         "outcome": oc.read_text() if oc.exists() else next((x for x in text.splitlines() if x.startswith("hop error")), None),
                         "output": sorted(kinds), "completion_tokens": (usage or {}).get("completion_tokens")})
        return rows


def _task_once(sc, ctx):
    """One F2.task session: (result ok, every step answered, step log)."""
    env, _ = user_home(ctx, sc.user, sc.idp)
    task, model = sc.request["task"], sc.request["model"]
    work = Path(env["HOME"]) / "work" / f"{task}-{time.time_ns()}"  # ns: a retry in the same second gets its own
    work.mkdir(parents=True)
    # Its own git root: opencode takes the nearest enclosing repository as the project, and the homes live inside this
    # checkout, so without this an --auto session could edit the sample's own files (r121: a "/compact" turn did).
    subprocess.run(["git", "init", "-q", str(work)], check=True)  # nosec B603 B607
    log = []
    rec = _ReplyRecorder(int(env["OCGW_PROXY_PORT"]), work.with_name(f"{work.name}-replies"))
    env = {**env, "OPENCODE_CONFIG_CONTENT": json.dumps(  # merged over the user's config: only the hop's port differs
        {"provider": {"ocgw": {"options": {"baseURL": f"http://127.0.0.1:{rec.port}/inference/v1"}}}})}
    try:
        return _task_steps(sc, env, work, task, model, log, rec)
    finally:
        rec.close()


def _task_steps(sc, env, work, task, model, log, rec):

    def step(prompt, m=model, cont=False):
        rc, out = _oc(env, work, m, prompt, cont)
        log.append({"rc": rc, "out": out[-300:]})
        return rc == 0

    def read(name):
        f = work / name
        return f.read_text().strip() if f.exists() else None
    if task == "write_file":
        ok = step("Create a file named hello.txt whose whole content is exactly: OCGW-WRITE-OK") and read("hello.txt") == "OCGW-WRITE-OK"
    elif task == "run_shell":
        ok = step("Use the shell tool to run exactly this command: echo OCGW-SHELL-OK > shell.txt") and read("shell.txt") == "OCGW-SHELL-OK"
    elif task == "multi_tool_edit":
        (work / "app.py").write_text('GREETING = "hello"\n')
        ok = (step("Read app.py, then edit it so GREETING is \"OCGW-EDIT-OK\". Change nothing else.")
              and read("app.py") == 'GREETING = "OCGW-EDIT-OK"')
    elif task == "compact":
        ok = (step("Remember the code word OCGW-ZEBRA. Reply with only: noted") and step("/compact", cont=True)
              and step("What was the code word? Reply with only the code word.", cont=True) and "OCGW-ZEBRA" in log[-1]["out"].upper())
    elif task == "switch_model_mid_session":
        ok = (step("Reply with only the word ONE") and "ONE" in log[-1]["out"].upper()
              and step("Reply with only the word TWO", m="ocgw/ow/glm-5", cont=True) and "TWO" in log[-1]["out"].upper())
    else:
        return None, False, {"reason": f"unknown task {task}"}
    return ok, all(x["rc"] == 0 for x in log), {"steps": log, **({} if ok else {"replies": rec.replies()})}


def _ocgw(env, *args, timeout=60):
    r = py_run([str(CLIENT), *args], env, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _local(env, method, path, key, body=None):
    c = http.client.HTTPConnection("127.0.0.1", int(env["OCGW_PROXY_PORT"]), timeout=120)
    try:
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None,
                  headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, r.read().decode(errors="replace")
    finally:
        c.close()


def _expire(tok: dict, kind: str = "access_token") -> dict:
    """The cached token (`kind`, the one ocgw is configured to send) with its exp claim moved an hour into the past
    (signature left as is: ocgw reads exp without verifying, and must then refresh and replace it; the gateway would
    refuse the edited token)."""
    head, body, sig = tok[kind].split(".")
    c = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    c["exp"], c["iat"] = int(time.time()) - 3600, int(time.time()) - 7200
    tok[kind] = ".".join([head, base64.urlsafe_b64encode(json.dumps(c).encode()).decode().rstrip("="), sig])
    tok["obtained_at"] = c["iat"]  # as old as the token: a refresh within the same second still shows as newer
    return tok


def drive_login(sc, ctx):
    """F2.client.login_pkce: `ocgw login --no-browser` with the hosted UI form played over HTTP (see r128's notes)."""
    import html  # noqa: PLC0415
    import re  # noqa: PLC0415
    import shutil as sh  # noqa: PLC0415
    import requests  # noqa: PLC0415
    if sc.idp != "cognito":
        return None, {"reason": "the hosted UI form is Cognito's; Okta and JumpCloud sign-ins are the interactive pass"}
    home = OUT / "homes" / f"login-{sc.user}"
    sh.rmtree(home, ignore_errors=True)
    cfg_dir = home / ".config" / "ocgw"
    cfg_dir.mkdir(mode=0o700, parents=True)
    (cfg_dir / "config.json").write_text(ctx.outs["ocgw_config"])
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", ""), "XDG_CONFIG_HOME": str(home / ".config")}
    with socket.socket() as probe:  # ocgw's redirect URI fixes its loopback port (8765, registered with the IdP)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as ocgw's HTTPServer does: TIME_WAIT is no clash
        try:
            probe.bind(("127.0.0.1", 8765))
        except OSError as e:
            return None, {"reason": f"127.0.0.1:8765 is in use (another `ocgw login`?), so ocgw cannot take the redirect: {e}"}
    proc = py_popen([str(CLIENT), "login", "--no-browser"], env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = ""
        for _ in range(5):  # "Open this URL to sign in:" then the URL
            line = proc.stderr.readline().strip()
            if line.startswith("https://"):
                break
        if not line.startswith("https://"):
            return False, {"reason": "ocgw printed no sign-in URL", "last": line[:200]}
        s = requests.Session()
        page = s.get(line, timeout=30)
        form = re.search(r'<form action="([^"]+)" method="POST" name="cognitoSignInForm"', page.text)
        csrf = re.search(r'name="_csrf" value="([^"]+)"', page.text)
        if not (form and csrf):
            return False, {"reason": "no hosted UI sign-in form", "status": page.status_code}
        action = urllib.parse.urljoin(page.url, html.unescape(form.group(1)))
        r = s.post(action, data={"_csrf": csrf.group(1), "username": sc.user, "password": ctx.outs["passwords"][sc.user],
                                 "signInSubmitButton": "Sign in"}, allow_redirects=False, timeout=30)
        to = r.headers.get("location", "")
        if not to.startswith("http://127.0.0.1:"):
            return False, {"reason": "the IdP did not redirect to ocgw", "status": r.status_code, "location": to[:120]}
        cb = requests.get(to, timeout=30)  # ocgw's loopback listener: it checks state, then exchanges the code
        out, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    tok_file = cfg_dir / "tokens.json"
    tok = json.loads(tok_file.read_text()) if tok_file.exists() else {}
    kind = json.loads(ctx.outs["ocgw_config"]).get("token_kind", "id_token")
    st = None
    if tok.get(kind):
        st, _ = http_call(ctx.base, "/inference/v1/models", "GET", {"Authorization": f"Bearer {tok[kind]}"}, None)
    ok = (proc.returncode == 0 and "Signed in as" in err and tok_file.exists()
          and (tok_file.stat().st_mode & 0o777) == 0o600 and bool(tok.get("refresh_token")) and st == 200)
    return ok, {"rc": proc.returncode, "callback_status": cb.status_code, "signed_in": err.strip()[-80:],
                "mode": oct(tok_file.stat().st_mode & 0o777) if tok_file.exists() else None,
                "has_refresh": bool(tok.get("refresh_token")), "gateway_status": st}


WRONG_GATEWAY = "https://wrong-gateway.example.invalid"  # what the user's file and the environment say
# python:3.12-slim, pinned by digest so every run tests the client on the same interpreter and base image.
MDM_IMAGE = "python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b"


def _mdm_run(ctx, home: Path, mdm: Path | None):
    """`ocgw opencode-config` in a clean Linux container, as this user, with or without the MDM file mounted."""
    vols = ["-v", f"{CLIENT}:/opt/ocgw.py:ro", "-v", f"{home}:/home/dev"]
    if mdm is not None:
        vols += ["-v", f"{mdm}:/etc/opencode-gateway/ocgw.json:ro"]
    args = ["--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/home/dev",
            "-e", f"OCGW_GATEWAY_URL={WRONG_GATEWAY}", *vols, MDM_IMAGE, "python3", "-B", "/opt/ocgw.py", "opencode-config"]
    return subprocess.run(["docker", "run", *args], executable=shutil.which("docker") or "docker",  # nosec B603 B607
                          capture_output=True, text=True, timeout=180)


def drive_mdm(sc, ctx):
    """F2.client.mdm_overrides_env: the MDM file wins over the user's config and the environment (see r128's notes)."""
    import shutil as sh  # noqa: PLC0415
    real = ocgw_config_for(ctx, sc.user, sc.idp)
    src_env, _ = user_home(ctx, sc.user, sc.idp)
    base = OUT / "homes" / f"mdm-{sc.user}"
    results = {}
    for label, with_mdm in (("with_mdm", True), ("without_mdm", False)):
        home = base / label
        sh.rmtree(home, ignore_errors=True)
        (home / ".config" / "ocgw").mkdir(mode=0o700, parents=True)
        # The ID token only: a copy of a rotating refresh token would be spent twice (the IdP then revokes both), and
        # opencode-config needs no refresh within the token's hour.
        tok = json.loads((Path(src_env["HOME"]) / ".config" / "ocgw" / "tokens.json").read_text())
        dst = home / ".config" / "ocgw" / "tokens.json"
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps({k: v for k, v in tok.items() if k != "refresh_token"}))
        (home / ".config" / "ocgw" / "config.json").write_text(json.dumps({**real, "gateway_url": WRONG_GATEWAY}))
        mdm = None
        if with_mdm:
            mdm = base / "ocgw.json"
            mdm.write_text(json.dumps(real))
        r = _mdm_run(ctx, home, mdm)
        oc = home / ".config" / "opencode" / "opencode.json"
        models = (json.loads(oc.read_text()).get("provider", {}).get("ocgw", {}).get("models") or {}) if oc.exists() else {}
        results[label] = {"rc": r.returncode, "models": len(models), "err": r.stderr[-200:]}
    ok = (results["with_mdm"]["rc"] == 0 and results["with_mdm"]["models"] > 0
          and results["without_mdm"]["rc"] != 0 and "could not list models" in results["without_mdm"]["err"])
    return ok, results


def drive_proxy(sc, ctx):
    """F2.client.*: ocgw as the developer runs it (token cache, refresh, proxy, opencode-config, logout), live."""
    case = sc.request["case"]
    if case == "login_pkce":
        return drive_login(sc, ctx)
    if case == "mdm_overrides_env":
        return drive_mdm(sc, ctx)
    env, _ = user_home(ctx, sc.user, sc.idp, tag="client" if case in ("token_refresh", "rotated_refresh", "logout_then_token") else "")
    home = Path(env["HOME"]) / ".config" / "ocgw"
    key = (home / "local.key").read_text().strip()
    chat = {"model": "ow/kimi-k3", "messages": [{"role": "user", "content": "Reply with the single word PONG."}], "max_tokens": 64}
    if case == "proxy_wrong_key":
        st, body = _local(env, "POST", "/inference/v1/chat/completions", "ocgw-wrong", chat)
        return st == 401, {"status": st, "body": body[:200]}
    if case == "proxy_path_denied":
        st, body = _local(env, "POST", "/inference/v1/embeddings", key, chat)
        st2, _ = _local(env, "DELETE", "/inference/v1/chat/completions", key)
        return (st, st2) == (404, 405), {"status": [st, st2], "body": body[:200]}
    if case == "proxy_stream":
        st, body = _local(env, "POST", "/inference/v1/chat/completions", key, {**chat, "stream": True})
        text = ""  # join the content deltas: the word can be split over chunks, and Mantle may end without [DONE]
        for line in body.splitlines():
            if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                with contextlib.suppress(ValueError, KeyError, IndexError, TypeError):
                    text += json.loads(line[5:])["choices"][0]["delta"].get("content") or ""
        return st == 200 and body.count("data:") > 1 and "PONG" in text.upper(), {"status": st, "text": text[:80], "chunks": body.count("data:")}
    if case == "opencode_config_models":
        doc = json.loads((Path(env["XDG_CONFIG_HOME"]) / "opencode" / "opencode.json").read_text())
        got = set(doc["provider"]["ocgw"]["models"])
        want = {m for m in S.MODELS if S.allowed(sc.user, m)}
        return got == want, {"models": sorted(got), "want": sorted(want)}
    if case in ("token_refresh", "rotated_refresh"):
        tf = home / "tokens.json"
        tok = json.loads(tf.read_text())
        old_rt = tok["refresh_token"]
        kind = json.loads((home / "config.json").read_text()).get("token_kind", "id_token")  # access_token on Cognito
        tok = _expire(tok, kind)
        tf.write_text(json.dumps(tok))
        rc, out, err = _ocgw(env, "token")
        new = json.loads(tf.read_text())
        ok = rc == 0 and out == new[kind] and new["obtained_at"] > tok["obtained_at"] and out != tok[kind]
        # Cognito does not rotate: the old refresh token is kept and works again. Okta (Rotate token after every use)
        # and JumpCloud rotate: the new one is saved in its place and the next refresh works with it.
        rotates = sc.idp != "cognito"
        if case == "rotated_refresh":
            tf.write_text(json.dumps(_expire(new, kind)))
            rc2, out2, _ = _ocgw(env, "token")
            rt = json.loads(tf.read_text()).get("refresh_token")
            ok = ok and rc2 == 0 and out2 != out and ((rt != old_rt) if rotates else (rt == old_rt))
        return ok, {"rc": rc, "err": err[-200:], "rotated": json.loads(tf.read_text()).get("refresh_token") != old_rt}
    if case == "logout_then_token":
        rc1, _, _ = _ocgw(env, "logout")
        rc2, _, err = _ocgw(env, "token")
        st, body = _local(env, "POST", "/inference/v1/chat/completions", key, chat)
        _HOMES.pop((sc.user, sc.idp, "client"))[1].terminate()  # the next client case signs in afresh
        return rc1 == 0 and rc2 != 0 and "not signed in" in err and st == 401, {"err": err[-160:], "proxy_status": st, "body": body[:160]}
    if case == "two_proxies_same_port":
        p2 = py_run([str(CLIENT), "proxy"], env, capture_output=True, text=True, timeout=30)
        st, _ = _local(env, "POST", "/inference/v1/chat/completions", key, chat)
        return p2.returncode != 0 and st == 200, {"second_rc": p2.returncode, "second_err": p2.stderr[-200:], "first_status": st}
    return None, {"reason": f"unknown client case {case}"}


def drive_terraform(sc, ctx):
    """F12.*: checks on the deployed stack itself (read-only AWS calls and a terraform plan)."""
    import boto3  # noqa: PLC0415
    check, name = sc.request["check"], ctx.outs.get("name", "ocgw")
    fns = [f"{name}-interceptor", f"{name}-usage-sync", f"{name}-dashboard"]
    if check == "plan_no_changes":  # exit code 0: the live stack matches the code (no drift, nothing out of band)
        r = subprocess.run(["terraform", f"-chdir={ctx.stack}", "plan", "-input=false", "-no-color", "-detailed-exitcode", "-lock=false"],  # nosec B603 B607
                           capture_output=True, text=True, timeout=900)
        return r.returncode == 0, {"rc": r.returncode, "tail": (r.stdout + r.stderr)[-400:]}
    if check == "kms_on_logs":
        logs = boto3.client("logs", region_name=ctx.region)
        got = {f: logs.describe_log_groups(logGroupNamePrefix=f"/aws/lambda/{f}")["logGroups"][0].get("kmsKeyId") for f in fns}
        return all(got.values()), {"kms": got}
    if check == "ddb_pitr":
        d = boto3.client("dynamodb", region_name=ctx.region).describe_continuous_backups(TableName=ctx.table)
        st = d["ContinuousBackupsDescription"]["PointInTimeRecoveryDescription"]["PointInTimeRecoveryStatus"]
        return st == "ENABLED", {"pitr": st}
    if check == "lambda_tracing":
        lam = boto3.client("lambda", region_name=ctx.region)
        got = {f: lam.get_function_configuration(FunctionName=f)["TracingConfig"]["Mode"] for f in fns}
        return all(v == "Active" for v in got.values()), {"tracing": got}
    if check == "iam_least_privilege":  # no role of the stack has a wildcard action or an AWS managed admin policy
        iam, lam = boto3.client("iam"), boto3.client("lambda", region_name=ctx.region)
        bad = []
        for f in fns:
            role = lam.get_function_configuration(FunctionName=f)["Role"].split("/")[-1]
            for pn in iam.list_role_policies(RoleName=role)["PolicyNames"]:
                doc = iam.get_role_policy(RoleName=role, PolicyName=pn)["PolicyDocument"]
                for st in doc["Statement"] if isinstance(doc["Statement"], list) else [doc["Statement"]]:
                    acts = st.get("Action", [])
                    acts = [acts] if isinstance(acts, str) else acts
                    if st.get("Effect") == "Allow" and any(a == "*" or a.endswith(":*") for a in acts):
                        bad.append(f"{role}/{pn}: {acts}")
            for ap in iam.list_attached_role_policies(RoleName=role)["AttachedPolicies"]:
                if ap["PolicyName"] in ("AdministratorAccess", "PowerUserAccess"):
                    bad.append(f"{role}: {ap['PolicyName']}")
        return not bad, {"wildcards": bad}
    # destroy_clean tears the stack down, so the person who owns the account runs it, last (README, Clean up).
    return None, {"reason": f"{check}: tears the stack down, so the account owner runs it last, by hand (README, Clean up)"}


def drive_websearch(sc, ctx):
    """F9.helper.*: POST /inference/websearch as the user; a 200 must carry results."""
    st, raw = http_call(ctx.base, "/inference/websearch", "POST", {"Authorization": f"Bearer {ctx.tokens.get(sc.user, sc.idp)}"},
                        json.dumps({"q": sc.request["q"], "count": 3}).encode())
    ok = st == sc.expect["status"] and (st != 200 or bool(json.loads(raw).get("results")))
    return ok, {"status": st, "body": raw[:200].decode(errors="replace")}


# ---------------------------------------------------------------- F11: the dashboard
# Each page names the one API call it cannot render without; "budgets" reads the org and group policy.
DASH_PAGES = {"overview": "/api/v2/overview", "users": "/api/v2/users", "user_detail": "/api/v2/users/alice",
              "groups": "/api/v2/groups", "budgets": "/api/v2/policy", "rate_limits": "/api/v2/ratelimits",
              "model_access": "/api/v2/models", "audit": "/api/v2/audit", "usage": "/api/v2/usage"}
DASH_GROUP = "ocgw-e2e-dash"  # created and removed by the group actions, so no real group's policy is touched
# alice: her limits come from the environment defaults, so each write here is set-then-unset and no other scenario
# reads a cap of hers (F5 budgets use carol, F6 rate limits use bob). A user with neither usage nor limits has no
# ledger row and answers 404, so the user chosen must be one that exists.
DASH_USER = "alice"


def dash_call(ctx, method, path, body=None, role="admin"):
    """One dashboard API call over the IAM-only Function URL, as one of the three roles (see this driver's notes)."""
    import boto3  # noqa: PLC0415
    import requests  # noqa: PLC0415
    from botocore.auth import SigV4Auth  # noqa: PLC0415
    from botocore.awsrequest import AWSRequest  # noqa: PLC0415
    url = ctx.outs["dashboard_url"].rstrip("/") + path
    data = json.dumps(body) if body is not None else None
    hdrs = {}
    if method != "GET":  # the API requires its CSRF header and a same-origin Origin on every write
        hdrs = {"content-type": "application/json", "x-ocgw-csrf": "1",
                "origin": f"https://{urllib.parse.urlsplit(url).netloc}"}
    if role == "viewer":
        hdrs["x-ocgw-client"] = "read-only-proxy"
    if role != "not_signed_in":
        req = AWSRequest(method=method, url=url, data=data, headers=dict(hdrs))
        creds = boto3.Session(region_name=ctx.region).get_credentials().get_frozen_credentials()
        SigV4Auth(creds, "lambda", ctx.region).add_auth(req)
        hdrs = dict(req.headers)
    r = requests.request(method, url, headers=hdrs, data=data, timeout=60)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"body": r.text[:200]}


def _user_limits(ctx, user=DASH_USER):
    """(version, the limits set on this user). `override` is exactly what the Users page edits, so restoring it writes
    back what was there - and a field that was not set goes back to not set, because None removes the attribute."""
    st, body = dash_call(ctx, "GET", f"/api/v2/users/{user}")
    assert st == 200, (st, body)
    return body.get("version"), dict(body.get("override") or {})


def _put_user(ctx, fields, version, user=DASH_USER):
    return dash_call(ctx, "PUT", f"/api/v2/users/{user}/limits", {**fields, "version": version})


def _group_policy(ctx, group=DASH_GROUP):
    """(version, row) for one group from the Groups page. A group with no policy of its own has no row, so the version
    is None and a write must send no version - which is how the API spells "create it"."""
    st, body = dash_call(ctx, "GET", "/api/v2/groups")
    assert st == 200, (st, body)
    row = next((g for g in body.get("groups") or [] if g.get("group") == group), {})
    return row.get("version"), row


def _put_group(ctx, fields, version, group=DASH_GROUP):
    return dash_call(ctx, "PUT", f"/api/v2/policy/groups/{urllib.parse.quote(group)}", {**fields, "version": version})


def _drop_group(ctx, group=DASH_GROUP):
    v, _ = _group_policy(ctx, group)
    if v is not None:
        dash_call(ctx, "DELETE", f"/api/v2/policy/groups/{urllib.parse.quote(group)}", {"version": v})


def _act_user_field(ctx, field, value):
    """Set one of a user's limits, check it came back, put the old value back."""
    version, before = _user_limits(ctx)
    st, body = _put_user(ctx, {field: value}, version)
    detail = {"status": st, "body": body if st != 200 else None}
    try:
        if st != 200:
            return False, detail
        _, after = _user_limits(ctx)
        detail["set_to"] = after.get(field)
        return after.get(field) == value, detail
    finally:
        v, _ = _user_limits(ctx)
        _put_user(ctx, {field: before.get(field)}, v)


def _act_group_models(ctx, models):
    version, _ = _group_policy(ctx)
    st, body = dash_call(ctx, "PUT", f"/api/v2/policy/groups/{DASH_GROUP}", {"models": models, "version": version})
    try:
        if st != 200:
            return False, {"status": st, "body": body}
        _, row = _group_policy(ctx)
        got = sorted((row.get("policy") or {}).get("models") or [])
        return got == sorted(models), {"status": st, "models": got}
    finally:
        _drop_group(ctx)


def _act_set_group_budget(ctx):
    version, _ = _group_policy(ctx)
    st, body = _put_group(ctx, {"monthly_usd": 7.5}, version)
    try:
        if st != 200:
            return False, {"status": st, "body": body}
        _, row = _group_policy(ctx)
        got = (row.get("policy") or {}).get("monthly_usd")
        return got == 7.5, {"status": st, "monthly_usd": got}
    finally:
        _drop_group(ctx)


def _act_block_message(ctx):
    st, pol = dash_call(ctx, "GET", "/api/v2/policy")
    assert st == 200, (st, pol)
    org = pol.get("org") or {}
    before, version = org.get("block_message"), org.get("version")
    msg = "Ask #ai-platform for a budget increase."
    st, body = dash_call(ctx, "PUT", "/api/v2/policy/org", {"block_message": msg, "version": version})
    try:
        if st != 200:
            return False, {"status": st, "body": body}
        _, pol2 = dash_call(ctx, "GET", "/api/v2/policy")
        return (pol2.get("org") or {}).get("block_message") == msg, {"status": st}
    finally:
        _, pol3 = dash_call(ctx, "GET", "/api/v2/policy")
        dash_call(ctx, "PUT", "/api/v2/policy/org",
                  {"block_message": before, "version": (pol3.get("org") or {}).get("version")})


def _act_group_with_space(ctx):
    """An IdP group whose name has a space: the UI encodes it, and the API must keep it one segment."""
    name = "ocgw e2e space"
    try:
        _drop_group(ctx, name)
        v, _ = _group_policy(ctx, name)
        st, body = _put_group(ctx, {"monthly_usd": 3.0}, v, group=name)
        if st != 200:
            return False, {"status": st, "body": body}
        _, row = _group_policy(ctx, name)
        got = (row.get("policy") or {}).get("monthly_usd")
        return got == 3.0, {"status": st, "group": name, "monthly_usd": got}
    finally:
        _drop_group(ctx, name)


def _act_double_click_save(ctx):
    """Save clicked twice: the same body, the same version, twice. The second is either idempotent or a 409, and the
    value the user asked for is what the ledger ends up with - never a half-applied edit."""
    version, before = _user_limits(ctx)
    body = {"monthly_usd": 11.0}
    try:
        st1, _ = _put_user(ctx, body, version)
        st2, _ = _put_user(ctx, body, version)
        _, after = _user_limits(ctx)
        return (st1 == 200 and st2 in (200, 409) and after.get("monthly_usd") == 11.0,
                {"first": st1, "second": st2, "monthly_usd": after.get("monthly_usd")})
    finally:
        v, _ = _user_limits(ctx)
        _put_user(ctx, {"monthly_usd": before.get("monthly_usd")}, v)


def _act_concurrent_edit(ctx):
    """Two admins editing the same user from the same page load: the second write must be refused with 409, not
    silently overwrite the first."""
    version, before = _user_limits(ctx)
    try:
        st1, _ = _put_user(ctx, {"monthly_usd": 12.0}, version)
        st2, body2 = _put_user(ctx, {"monthly_usd": 13.0}, version)  # the version it loaded with, now stale
        _, after = _user_limits(ctx)
        return (st1 == 200 and st2 == 409 and after.get("monthly_usd") == 12.0,
                {"first": st1, "second": st2, "monthly_usd": after.get("monthly_usd"),
                 "message": (body2 or {}).get("error")})
    finally:
        v, _ = _user_limits(ctx)
        _put_user(ctx, {"monthly_usd": before.get("monthly_usd")}, v)


def _act_invalid_number(ctx):
    """A budget that is not a number must be refused, naming the field, and nothing may change."""
    version, before = _user_limits(ctx)
    st, body = _put_user(ctx, {"monthly_usd": "abc"}, version)
    _, after = _user_limits(ctx)
    unchanged = after.get("monthly_usd") == before.get("monthly_usd")
    # "succeeded" is False here, which is what this scenario expects: a 400 that leaves the ledger alone.
    return st == 200, {"status": st, "field": (body or {}).get("field"), "unchanged": unchanged,
                       "error": (body or {}).get("error")}


def _hourly_row(ctx, user=DASH_USER):
    st, body = dash_call(ctx, "GET", f"/api/v2/users/{user}")
    assert st == 200, (st, body)
    return next(r for r in body["limits"] if r["period"] == "Hourly")


def _act_repair(ctx, plant):
    """Repair record: with `plant`, the user's current HOUR item gets a spend_usd the gateway cannot read (a hand
    edit); the dashboard must flag it, the repair must remove just that value with an audit row, and the row must read
    again. Without `plant`, a repair of a readable record must be refused (409) and change nothing. The window's own
    spend_usd is put back either way, by ADDing it, so a web search charged meanwhile is kept."""
    if time.gmtime().tm_min == 59:  # the server picks its own hour: leave a minute for the three round trips
        time.sleep(61 - time.gmtime().tm_sec)
    key = {"pk": {"S": f"USER#{DASH_USER}"}, "sk": {"S": policy.window_sk("HOUR", time.gmtime())}}
    old = _ddb(ctx).get_item(TableName=ctx.table, Key=key, ConsistentRead=True).get("Item")
    planted = False
    try:
        if plant:
            _ddb(ctx).update_item(TableName=ctx.table, Key=key, UpdateExpression="SET spend_usd = :b",
                                  ExpressionAttributeValues={":b": {"S": "e2e-hand-edit"}})
            planted = True
            flagged = _hourly_row(ctx)
            if not flagged.get("unreadable") or flagged.get("spent") is not None:
                return False, {"flagged": flagged}
        st, body = dash_call(ctx, "POST", f"/api/v2/users/{DASH_USER}/repair", {"period": "Hourly", "reason": "e2e: repair a hand edit"})
        row = _hourly_row(ctx)
        detail = {"status": st, "removed": (body.get("item") or {}).get("removed") if isinstance(body, dict) else None,
                  "audit": (body.get("audit") or {}).get("action") if isinstance(body, dict) else None, "unreadable_after": row.get("unreadable")}
        if not plant:
            return st == 200, detail  # expected False: a 409, the record untouched
        return st == 200 and detail["removed"] == ["spend_usd"] and detail["audit"] == "repair_usage_record" and row.get("unreadable") is False, detail
    finally:
        now = _ddb(ctx).get_item(TableName=ctx.table, Key=key, ConsistentRead=True).get("Item") or {}
        if planted and "S" in now.get("spend_usd", {}):  # the repair did not run: take the planted value off first
            _ddb(ctx).update_item(TableName=ctx.table, Key=key, UpdateExpression="REMOVE spend_usd")
        if planted and old and "N" in old.get("spend_usd", {}):  # the repair removed it: ADD it back onto any new charge
            _ddb(ctx).update_item(TableName=ctx.table, Key=key, UpdateExpression="ADD spend_usd :v", ExpressionAttributeValues={":v": old["spend_usd"]})
        if planted and not old:
            _drop_if_bare(ctx, key)


DASH_ACTIONS = {
    "set_user_budget": lambda ctx: _act_user_field(ctx, "monthly_usd", 9.0),
    "set_rpm": lambda ctx: _act_user_field(ctx, "rpm", 42),
    "set_group_budget": _act_set_group_budget,
    "allow_model": lambda ctx: _act_group_models(ctx, ["ow/kimi-k3", "ow/glm-5"]),
    "deny_model": lambda ctx: _act_group_models(ctx, ["ow/kimi-k3"]),
    "block_message": _act_block_message,
    "group_with_space": _act_group_with_space,
    "double_click_save": _act_double_click_save,
    "concurrent_edit": _act_concurrent_edit,
    "invalid_number": _act_invalid_number,
    "repair_record": lambda ctx: _act_repair(ctx, plant=True),
    "repair_readable_record": lambda ctx: _act_repair(ctx, plant=False),
}


def drive_dashboard(sc, ctx):
    """F11.*: the dashboard API over the IAM-only Function URL, as each role, and every admin action."""
    r = sc.request
    if "action" in r:
        done, detail = DASH_ACTIONS[r["action"]](ctx)
        return done == sc.expect["ok"], {"action": r["action"], "succeeded": done, **detail}
    status, body = dash_call(ctx, "GET", DASH_PAGES[r["page"]], role=r["role"])
    detail = {"role": r["role"], "status": status,
              "keys": sorted(body)[:6] if isinstance(body, dict) else None}
    if r["role"] == "not_signed_in":  # refused by the Function URL's IAM auth, before the Lambda runs
        return status == 403, detail
    return (status == 200) == sc.expect["visible"], detail


DRIVERS = {"http": drive_http, "mcp": drive_mcp, "opencode": drive_opencode, "proxy": drive_proxy, "terraform": drive_terraform,
           "websearch": drive_websearch, "dashboard": drive_dashboard}


def main(argv=None):
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # atexit restores (_restore_at_exit) run on a kill too
    p = argparse.ArgumentParser()
    p.add_argument("--stack", type=Path, default=HERE.parents[1] / "terraform")
    p.add_argument("--idp", default=None)
    p.add_argument("--only", default="")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    todo = [s for s in S.scenarios() if s.feature.startswith(a.only) and (a.idp is None or s.idp == a.idp)]
    if a.limit:
        todo = todo[:a.limit]
    if a.dry_run:
        print(f"{len(todo)} scenarios selected")
        return 0

    class Ctx:
        pass
    ctx = Ctx()
    outs = tf_outputs(a.stack)
    ctx.stack = a.stack
    ctx.outs, ctx.base, ctx.tokens = outs, outs["gateway_base_url"], Tokens(outs, outs["region"])
    ctx.region, ctx.table, ctx.websearch = outs["region"], outs["usage_table"], outs.get("websearch_mcp_url", "")
    ctx.ledger_user = lambda user, idp: user  # ledger names are var.users keys for every IdP (identity_map maps sub -> name)
    results, counts = [], {"pass": 0, "fail": 0, "n/a": 0, "no_driver": 0, "error": 0, "retried": 0}
    for sc in todo:
        drv = DRIVERS.get(sc.kind)
        t0 = time.time()
        try:
            ok, detail = drv(sc, ctx) if drv else (None, {"reason": f"no driver for kind {sc.kind}"})
        except Exception as e:  # noqa: BLE001 - recorded per scenario
            ok, detail = "error", {"error": f"{type(e).__name__}: {e}"[:400]}
        outcome = ("pass" if ok is True else "fail" if ok is False else "error" if ok == "error"
                   else ok if ok == "n/a" else "no_driver")
        counts[outcome] += 1
        retried = detail.get("retried") is True  # a driver's second attempt (testing.md): counted and shown, never hidden
        counts["retried"] += retried
        results.append({"id": sc.id, "feature": sc.feature, "idp": sc.idp, "user": sc.user, "outcome": outcome,
                        "ms": round((time.time() - t0) * 1000), **detail})
        print(f"{outcome:9} {sc.id}" + (" (after a retry)" if retried else ""))
    OUT.mkdir(exist_ok=True)  # the proxies stop at exit (user_home registers each with atexit)
    (OUT / "results.json").write_text(json.dumps({"counts": counts, "results": results}, indent=1))
    print(json.dumps(counts))
    return 0 if counts["fail"] == counts["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
