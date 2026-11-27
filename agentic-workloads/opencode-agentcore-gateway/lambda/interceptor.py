# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""AgentCore Gateway REQUEST interceptor for opencode.

The gateway has already validated the caller's JWT (Okta, JumpCloud or the Cognito stand-in). For every request this
Lambda:
  1. reads the user and groups from the token (USER_CLAIM, GROUP_CLAIM; IDENTITY_MAP renames IdP ids),
  2. accepts only the OpenAI-compatible routes opencode uses: POST .../v1/chat/completions, POST .../v1/responses and
     GET .../v1/models; anything else is refused, so nothing reaches a target unchecked,
  3. checks the model against the caller's group allow-list (policy.py: DynamoDB POLICY items, else GROUP_MODELS),
  4. checks the caller's hourly, daily and monthly $ budget and per-user requests-per-minute cap (ledger in DynamoDB),
  5. rewrites "ow/<alias>" to the Mantle model ID the `ow` target serves and adds the caller's Mantle project
     (OpenAI-Project header), so Mantle attributes the usage to that user and usage_sync can price it,
  6. normalises two opencode request shapes that open-weight models on Mantle reject,
  7. answers GET /v1/models with the models the caller may use, and the /websearch helper endpoint,
  8. on the web search MCP gateway (websearch.tf): gates tools/call on model access, budget and rpm, and charges it.

Any failure answers with a generic error and keeps the detail in the log: a request that was not checked is never
forwarded.
"""
import base64
import concurrent.futures
import functools
import http.client
import itertools
import json
import math
import os
import re
import socket
import ssl
import threading
import time
import urllib.parse

import boto3
from botocore.config import Config

import policy

USER_CLAIM = os.environ.get("USER_CLAIM", "username")
GROUP_CLAIM = os.environ.get("GROUP_CLAIM", "cognito:groups")
GROUP_MODELS = json.loads(os.environ.get("GROUP_MODELS", "{}"))
TABLE = os.environ.get("TABLE")
PROJECTS = json.loads(os.environ.get("PROJECTS", "{}"))  # ledger user -> Mantle project id
IDENTITY_MAP = json.loads(os.environ.get("IDENTITY_MAP", "{}"))  # IdP user id -> ledger user name
MAP_REQUIRED = os.environ.get("IDENTITY_MAP_REQUIRED") == "1"  # Okta and JumpCloud (policy.tf)


def ledger_user(raw):
    """Ledger name for an IdP user id. With an identity map (required for Okta and JumpCloud) only mapped ids are
    users; any other id is refused (returns ""), so nobody can pick a claim value that names someone else's ledger.
    Without a map (the Cognito stand-in, whose username the user cannot change) the claim value is the name.
    On an Okta or JumpCloud gateway the map is the only source of names even when it arrives empty: a CONFIG/DIRECTORY
    item written with identity_map = {} cuts every user off, rather than authorizing by the raw claim from then on."""
    if IDENTITY_MAP or MAP_REQUIRED:
        return IDENTITY_MAP.get(raw, "")
    return raw
MANTLE_IDS = json.loads(os.environ.get("MANTLE_IDS", "{}"))  # "ow/<alias>" -> Mantle model id
MAX_OUT = json.loads(os.environ.get("MAX_OUT", "{}"))  # "ow/<alias>" -> max output tokens
MODEL_ALIASES = json.loads(os.environ.get("MODEL_ALIASES", "{}"))
FALLBACKS = json.loads(os.environ.get("FALLBACKS", "{}"))  # ow/<model> -> [ow/<fallback model>, ...] (fallback.tf)
MODEL_CATALOG = json.loads(os.environ.get("MODEL_CATALOG", "[]"))
WEBSEARCH_MCP_URL = os.environ.get("WEBSEARCH_MCP_URL", "")
WEBSEARCH_USD = float(os.environ.get("WEBSEARCH_USD", "0.007"))  # AgentCore Web Search: $7 per 1,000 queries
# Headers a client must never set itself: the interceptor is the only source of the caller's project.
INJECTED = ("openai-project", "openai-organization", "x-ocgw-user", "x-ocgw-groups")
ROUTES = {("POST", "/v1/chat/completions"), ("POST", "/v1/responses"), ("GET", "/v1/models"), ("POST", "/websearch")}

ddb = boto3.client("dynamodb") if TABLE else None
_DISPLAY_DDB = []


def display_ddb():
    """The client for the groups record (record_groups): display data in the request, so one try per call, 0.5 s to
    connect and 0.5 s to read, never the default client's retries and backoff; a call costs at most about 1 s when
    DynamoDB is unreachable. Made on first use, as most invocations never need it."""
    if TABLE and not _DISPLAY_DDB:
        _DISPLAY_DDB.append(boto3.client("dynamodb", config=Config(connect_timeout=0.5, read_timeout=0.5,
                                                                    retries={"total_max_attempts": 1})))
    return _DISPLAY_DDB[0] if _DISPLAY_DDB else None


def _no_constant(name):
    raise ValueError(f"{name} is not JSON")  # NaN / Infinity: Python accepts them, strict parsers downstream do not


def claims_of(headers):
    auths = [v for k, v in headers.items() if k.lower() == "authorization"]
    if len(auths) != 1:  # none, or case-variant duplicates: the gateway may have validated a different copy
        return {}
    auth = auths[0] if isinstance(auths[0], str) else ""
    parts = auth.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return {}
    try:
        p = parts[1].split(".")[1]
        c = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    except (IndexError, ValueError, RecursionError):
        return {}
    return c if isinstance(c, dict) else {}


def user_claim(claims):
    """The user claim, only if it is a non-empty string (a list or object would crash the lookups below)."""
    u = claims.get(USER_CLAIM)
    return u if isinstance(u, str) and u else None


def groups_of(claims):
    return policy.groups_of(claims, GROUP_CLAIM)


def policies():
    return policy.policy_items(ddb, TABLE) if ddb else {}


def allow_list(groups):
    """The caller's model patterns: POLICY GROUP#<g>.models in DynamoDB, else GROUP_MODELS from env. Resolve once per
    request and test models against it with policy.model_allowed."""
    pol = policies()  # already cleaned (read_item); only the groups' model lists, no limit resolution on this path
    return policy.group_models({g: pol.get(f"GROUP#{g}", {}) for g in groups}, groups, GROUP_MODELS)[0]


ENV_LIMITS = policy.env_limits(os.environ)  # the same parsing as the dashboard
RESETS = {"Hourly": "at the top of the hour (UTC)", "Daily": "at 00:00 UTC", "Monthly": "on the 1st (UTC)"}


def spend(user, groups=(), read=None):
    """Spend and caps per window: {"Monthly": (spent, cap), "Daily": ..., "Hourly": ...}, the user's rpm cap, and the
    resolved limits (policy.resolve: user override > groups > org > env). `read`, a dict, receives the BUDGET item
    ("budget") so record_groups need not read it again, and the time the windows were picked ("at"), so the request
    is counted in the windows it was checked against."""
    at = time.time()
    g = time.gmtime(at)
    (month, _), (day, _), (hour, _) = windows_of(g)  # the same items count_request and charge_search write
    keys = {"BUDGET": "BUDGET", "Monthly": month, "Daily": day, "Hourly": hour}
    # Fewer, shorter retries than the usage sync: this is on the request path. Raises (fails closed) if keys stay unread.
    got = policy.batch_get(ddb, TABLE, [(f"USER#{user}", sk) for sk in keys.values()], attempts=4, base=0.05)
    items = {sk: it for (_, sk), it in got.items()}
    if read is not None:
        read["budget"], read["at"] = items.get("BUDGET"), at
    eff = policy.resolve(policy.read_item(items.get("BUDGET")), policies(), list(groups), ENV_LIMITS, GROUP_MODELS)
    # model_spend_usd: SET by usage_sync from Mantle metrics; spend_usd: web search charges ADDed by this function.
    read_w = {w: policy.window_read(items.get(keys[w])) for w in ("Hourly", "Daily", "Monthly")}
    out = {w: (read_w[w][0], eff["caps"][w]["cap"]) for w in read_w}
    # Capped windows whose spend record cannot be trusted (policy.window_read): gate() refuses on them. A window with
    # no cap limits nothing, so a bad record there blocks nothing (r141); the dashboard flags it on every window.
    out["readable"] = {w: read_w[w][1] for w in read_w}  # over_budget applies policy.refused to each window
    return out, eff["rpm"]["value"], eff


def take_rpm(user, limit):
    """Counts this request in the user's current minute; False once the minute already holds `limit` requests."""
    if limit <= 0:  # 0 blocks: the condition below would let the first request of each minute through
        return False
    minute = time.strftime("RPM#%Y-%m-%dT%H:%M", time.gmtime())
    try:
        ddb.update_item(TableName=TABLE, Key={"pk": {"S": f"USER#{user}"}, "sk": {"S": minute}},
                        UpdateExpression="ADD requests :one SET #ttl = :ttl", ConditionExpression="attribute_not_exists(requests) OR requests < :lim",
                        ExpressionAttributeNames={"#ttl": "ttl"},
                        ExpressionAttributeValues={":one": {"N": "1"}, ":lim": {"N": str(limit)}, ":ttl": {"N": str(int(time.time()) + 600)}})
        return True
    except ddb.exceptions.ConditionalCheckFailedException:
        return False


def token_iat(claims):
    """The token's issue time (iat, seconds), 0 when absent, not a number or more than five minutes ahead: orders
    the groups writes. A future iat is no real token's; kept, it would outrank every later token for good (r194)."""
    v = claims.get("iat")
    # Finite only: json.loads reads 1e999 as inf, and int(inf) would raise on every request carrying it.
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 < v <= time.time() + 300
    return int(v) if ok else 0


_SETTLED = {}  # user -> (iat, sorted groups) of the newest token whose record this container settled
_UNREAD = object()  # record_groups: the request did not read the BUDGET item (None: it read it, and there is none)


def may_record(user, groups, iat, item=_UNREAD):
    """False when record_groups would return at once (this token settled here, or no BUDGET item), so a caller can
    skip handing it to a thread."""
    done_iat, done_groups = _SETTLED.get(user, (-1, None))
    return item is not None and not (iat < done_iat or (iat, sorted(groups)) == (done_iat, done_groups))


def record_groups(user, groups, iat, item=_UNREAD):
    """Keep the token's groups on the user's BUDGET item: the dashboard has no directory of its own, and
    policy.budget_groups files spend under these groups. Called once per request, from the handler's exit
    (finish), for a ledger user, whatever the outcome: a group with no models yet reaches the dashboard whichever
    path its member takes (a refusal, or GET /v1/models that lists nothing).

    Ordered by the token's issue time (iat): written only over a token issued no later (groups_iat <= iat), so a user
    holding an old token and a new one after an IdP change keeps the new groups, and the recorded iat moves forward
    with each new token even when the groups are the same, so no older token can write afterwards. That is one
    conditional write per user per new token (about hourly), the cost of the ordering. Never re-reads: when the
    request read the BUDGET item (spend), an item that already holds these groups from this token or a newer one,
    or no item at all, costs nothing (a missing item is not settled, so it is tried again once it exists);
    otherwise the conditional write itself decides. Each container remembers the highest iat it
    settled per user with its groups, so later requests on that token, or on an older one, cost nothing, while a token
    of the same iat with other groups (no iat claim, or two sign-ins in one second) is still recorded. Never creates the item
    (attribute_exists): a user Terraform removed is not re-created; one re-created by Terraform shows its seeded
    group until the user's next token. In the request, never on a background thread Lambda can finish minutes
    later, through display_ddb (one try, about 1 s at most). Display data only: errors are logged, never raised."""
    key = (iat, sorted(groups))
    if not may_record(user, groups, iat, item):
        return  # settled here already, or read and there is no BUDGET item (nothing to write, not settled)
    try:
        held_iat = 0
        if item is not _UNREAD:
            held = policy.token_groups(item)
            # An unreadable stamp (a hand edit) is never a newer token: -1, so the write below replaces it.
            held_iat = -1 if (v := policy.number(item, "groups_iat")) is None else v
            if held_iat > iat or (held_iat == iat and held is not None and sorted(held) == sorted(groups)
                                  and "groups_seen_at" not in item):
                _SETTLED[user] = key
                return
        db = display_ddb()
        if not db:
            return
        try:
            db.update_item(
                TableName=TABLE, Key={"pk": {"S": f"USER#{user}"}, "sk": {"S": "BUDGET"}},
                UpdateExpression="SET token_groups = :g, groups_iat = :i REMOVE groups_seen_at",  # groups_seen_at: r129-r130
                # Ordered by iat; a groups_iat that is not a number (a hand edit) is replaced, never taken as newer.
                # NOT attribute_type is also true for a missing groups_iat (the first record).
                ConditionExpression="attribute_exists(pk) AND (NOT attribute_type(groups_iat, :n) OR groups_iat <= :i)",
                ExpressionAttributeValues={":g": {"L": [{"S": g} for g in groups]}, ":i": {"N": str(iat)}, ":n": {"S": "N"}},
                ReturnValuesOnConditionCheckFailure="ALL_OLD")
        except db.exceptions.ConditionalCheckFailedException as e:
            old = (getattr(e, "response", None) or {}).get("Item")
            if not old:
                return  # no BUDGET item: not settled, so tried again once it exists
            # else a newer token is recorded already: settled
        _SETTLED[user] = key
    except Exception as e:  # noqa: BLE001 - display data only; never block a request on it
        print(json.dumps({"outcome": "record_groups_error", "error": str(e)[:200]}))


def finish(log, outcome, t0, user, groups, iat, read, record=True):
    """Every handler's exit: record the caller's groups (record_groups, for a user with a ledger project), then log
    the outcome. `read` is where spend() left the BUDGET item, if it ran. A write limits() already started beside the
    gate ("record") is awaited here, so the logged ms includes it; http_handler's finally awaits it too, for the
    exits that never reach finish (a raise). record=False when the request's own MCP call records them."""
    if "record" in read:
        read["record"].result()  # the write limits() started; waited here so the logged ms includes it
    elif record and user and ddb and user in PROJECTS:
        record_groups(user, groups, iat, read.get("budget", _UNREAD))
    print(json.dumps({**log, "outcome": outcome, "ms": round((time.time() - t0) * 1000, 1)}))


# boto3 clients are thread-safe; kept across invocations. _POOL runs a request's own writes, the gate's and
# charge_search's three window writes at once (_all); count_request's display-only writes run on _BG.
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=3)
# Display-only writes (request counts) run here, never waited on by the request: a separate pool, so
# count_request fanning out on _POOL can never wait on a thread of its own pool. Lambda freezes the container after
# the reply; a write still in flight finishes when the container next runs, or is lost with it, which only costs a
# count on the dashboard, never a check.
_BG = concurrent.futures.ThreadPoolExecutor(max_workers=3)  # 3: one count's MONTH, DAY and HOUR writes at once
NO_PROJECT = "{user} has no usage project yet; ask your admin to add the user to the gateway."


def windows_of(g):
    """(sort key, TTL seconds) of the MONTH/DAY/HOUR ledger items for time g."""
    return tuple((policy.window_sk(k, g), policy.WINDOW_TTL[k]) for k in ("MONTH", "DAY", "HOUR"))


def over_budget(windows):
    """(window, spent, cap, readable) of the first window the gate refuses on, Hourly first, or None: policy.refused,
    the rule the dashboard shows as blocked. `readable` (from spend) is absent in a caller that has only sums."""
    for w in ("Hourly", "Daily", "Monthly"):
        spent, cap = windows[w]
        readable = windows.get("readable", {}).get(w, True)
        if policy.refused(readable, spent, cap):
            return w, spent, cap, readable
    return None


def _all(calls, labels):
    """Run the DynamoDB writes concurrently (one round trip instead of three on the request path); after all have
    finished, raise one error naming every item that failed (labels[i] for calls[i]), so the log lists each window."""
    errors = [f.exception() for f in [_POOL.submit(c) for c in calls]]
    if failed := [(label, e) for label, e in zip(labels, errors) if e]:
        raise RuntimeError("; ".join(f"{label}: {type(e).__name__}" for label, e in failed)
                           + f" ({failed[0][1]})") from failed[0][1]


def add_to_windows(user, add, names=None, values=None, also_set="", at=None, background=False):
    """ADD `add` on the user's MONTH, DAY and HOUR items, setting each item's ttl on first write (windows_of: the key
    format and how long each window is kept), plus `also_set`. values may be a function of the time struct. at: the
    time the request was allowed (a background write must land in that hour, not the one it happens to run in).
    background: each write is handed to _BG and not waited for (errors logged), so display data never delays a call
    and never queues on _POOL ahead of a request's own writes (charge_search). count_request and charge_search share it."""
    t = at if at is not None else time.time()
    g, now = time.gmtime(t), int(t)
    expr = f"ADD {add} SET #ttl = if_not_exists(#ttl, :ttl)" + (f", {also_set}" if also_set else "")
    vals = values(g) if callable(values) else (values or {})
    keys = windows_of(g)
    calls = [functools.partial(ddb.update_item, TableName=TABLE, Key={"pk": {"S": f"USER#{user}"}, "sk": {"S": sk}},
                               UpdateExpression=expr, ExpressionAttributeNames={"#ttl": "ttl", **(names or {})},
                               ExpressionAttributeValues={":ttl": {"N": str(now + keep)}, **vals})
             for sk, keep in keys]
    if background:
        for c in calls:
            _BG.submit(c).add_done_callback(_log_bg_error)
    else:
        _all(calls, [sk for sk, _ in keys])


def _log_bg_error(f):
    if (e := f.exception()) is not None:  # display data only: logged, never raised into a request
        print(json.dumps({"outcome": "background_write_error", "error": str(e)[:200]}))


def count_request(user, model=None, at=None):
    """ADD one request to the user's MONTH/DAY/HOUR items, in total and for the model (m|<model>|req), so the model
    and group views can show requests. usage_sync never writes these on USER# items, so they do not clash."""
    if not ddb:
        return
    try:
        add_to_windows(user, "requests :one" + (", #mr :one" if model else ""), {"#mr": policy.model_attr(model, "req")} if model else {},
                       lambda g: {":one": {"N": "1"}, ":seen": {"S": time.strftime("%Y-%m-%dT%H:%M:%SZ", g)}}, also_set="last_seen_at = :seen",
                       at=at, background=True)
    except Exception as e:  # noqa: BLE001 - display data only; never block an allowed request on it
        print(json.dumps({"outcome": "count_request_error", "error": str(e)[:200]}))


def answer(status, payload):
    return {"interceptorOutputVersion": "1.0", "http": {"transformedGatewayResponse": {
        "statusCode": status, "contentType": "application/json", "body": base64.b64encode(json.dumps(payload).encode()).decode()}}}


def deny(status, etype, message, code=None):
    """An OpenAI-style error body, which opencode (via the AI SDK) shows to the user as is."""
    return answer(status, {"error": {"message": message, "type": etype, "code": code or etype, "param": None}})


def barred_hops(model, patterns):
    """The fallback hops of model that the caller's allow-list does not permit. Used both to refuse a request
    (fallback_denied) and to leave such a model out of /models, so the listing and the gate cannot drift."""
    return [f for f in FALLBACKS.get(model, []) if not policy.model_allowed(f, patterns)]


def models_for(groups):
    """The caller's model list (OpenAI shape): the catalog entries the allow-list permits. Aliases are accepted on
    requests but never listed, so a client's model picker shows each model once. A model whose fallback chain names a
    model the caller may not use is left out too: every request for it is refused with fallback_denied."""
    rows = [{"id": m["id"], "object": "model", "created": 1767225600, "owned_by": "amazon-bedrock", "name": m.get("display_name", m["id"])}
            for m in usable_models(allow_list(groups))]
    return {"object": "list", "data": rows}


def usable_models(patterns):
    """The catalog entries a caller with these allow-list patterns can actually call (allowed, and no fallback hop
    barred). Web search needs at least one: a pattern that matches no served model gives no search either."""
    return [m for m in MODEL_CATALOG if policy.model_allowed(m["id"], patterns) and not barred_hops(m["id"], patterns)]


class SearchRefused(Exception):
    """The web search gateway's own gate (mcp_handler) refused the call: budget, rate limit or no model access."""
    HTTP = {-32002: (400, "invalid_request_error", "budget_exceeded"), -32003: (429, "rate_limit_error", None),
            -32004: (403, "permission_error", None)}

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


SEARCH_REPLY_MAX = 2 * 1024 * 1024  # bytes read from the web search gateway
SEARCH_DEADLINE_S = 15  # the whole search, from the request to the reply (the interceptor Lambda's timeout is 30 s)


class SearchTimeout(RuntimeError):
    """The web search used up SEARCH_DEADLINE_S."""

    def __init__(self):
        super().__init__(f"web search gave no reply within {SEARCH_DEADLINE_S} s")


def search_chunks(r, timed_out):
    """The web search reply's bytes as they arrive (read1: up to 64 KB of what is there), for both reply types, up to
    SEARCH_REPLY_MAX bytes. It stops when the response is complete (http.client closes it then), so a finished reply is
    never read past. The deadline is the watchdog's (web_search): once it fires, the socket is shut and reads end."""
    seen = 0
    while not r.isclosed():
        chunk = r.read1(65536)
        if not chunk:
            if timed_out.is_set():  # the watchdog shut the socket; bytes read before it are still used
                raise SearchTimeout()
            return
        seen += len(chunk)
        if seen > SEARCH_REPLY_MAX:
            raise RuntimeError("web search reply too large")
        yield chunk


_SSE_EOL = re.compile(rb"\r\n|\r|\n")


def sse_lines(chunks):
    """Lines of a text/event-stream from raw byte chunks, split only where the SSE spec ends a line (CRLF, LF, or a
    bare CR), so a U+2028 or U+0085 inside a JSON string never cuts a line. A CR ends its line at once (a reply whose
    last blank line is a bare CR is dispatched without waiting for more bytes); an LF straight after it, even in the
    next chunk, is the rest of that CRLF and is skipped. Yields the last, unterminated line at the end of the stream."""
    buf, after_cr = bytearray(), False
    for chunk in chunks:
        if not chunk:  # an empty read says nothing: a CR held from the last chunk still waits for its LF
            continue
        if after_cr and chunk[:1] == b"\n":
            chunk = chunk[1:]
        scan = len(buf)  # the bytes already held contain no line end: look only at the new ones
        buf += chunk  # in place: a long line in small chunks stays linear
        after_cr, pos = False, 0
        while m := _SSE_EOL.search(buf, max(pos, scan)):
            yield buf[pos:m.start()].decode("utf-8")
            pos = m.end()
            after_cr = m.group() == b"\r" and pos == len(buf)  # a CR last in the chunk: an LF may follow next
        del buf[:pos]
    if buf:
        yield bytes(buf).decode("utf-8")


def sse_reply(chunks):
    """The first JSON-RPC reply (an event carrying a result or an error) in a text/event-stream read from raw byte
    chunks (search_chunks: size cap and deadline), so the caller returns as soon as it arrives even if the server keeps
    the stream open. Notification and priming events before it are skipped."""
    data = []
    # Per the SSE spec: "data" alone is the field with an empty value; one space after the colon is dropped; a blank
    # line (or the end of the stream) dispatches the event.
    for line in itertools.chain(sse_lines(chunks), [""]):
        if line == "data" or line.startswith("data:"):
            v = line[5:]
            data.append(v[1:] if v.startswith(" ") else v)
            continue
        if line or not data:
            continue
        body, data = "\n".join(data), []
        if not body.strip():  # a priming event (an id with an empty data field) carries no message
            continue
        msg = json.loads(body)
        if isinstance(msg, dict) and ("result" in msg or "error" in msg):
            return msg
    raise RuntimeError("web search stream had no reply")


DNS_STALLED_MAX = 4  # web search DNS lookups in flight (stalled ones included): beyond this, fail fast
_DNS_SLOTS = threading.BoundedSemaphore(DNS_STALLED_MAX)


@functools.cache
def tls_context():
    """One TLS client context for the web search gateway, built once per container (it loads the CA bundle)."""
    return ssl.create_default_context()


def search_connect(u, deadline):
    """A TCP connection to the web search gateway inside the deadline: each address DNS gives may use only the time
    left (socket.create_connection would give every address the whole timeout)."""
    # DNS on its own thread, waited for only the time left: getaddrinfo cannot be interrupted, and a stalled resolver
    # (glibc: 5 s x 2 attempts per nameserver) would otherwise run past the deadline. A lookup given up on just ends
    # in the resolver's own time; it holds no connection and charges nothing.
    # Each lookup has its own short-lived thread, so one stalled lookup never makes a later search wait behind it (a
    # shared pool would). Lambda runs one request at a time per container, so at most one starts per search; if
    # DNS_STALLED_MAX given-up lookups are still running, the resolver is down and the search fails at once.
    slots = _DNS_SLOTS  # released on the object acquired, whatever the module name points at by then
    if not slots.acquire(blocking=False):
        raise RuntimeError("web search DNS is not answering")
    got = {}

    def resolve():
        try:
            got["addrs"] = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80),
                                              type=socket.SOCK_STREAM)
        except Exception as e:  # noqa: BLE001 - any lookup error (OSError, a bad IDNA label) is the caller's to see
            got["error"] = e
        finally:
            slots.release()
    try:
        t = threading.Thread(target=resolve, daemon=True)
        t.start()
    except BaseException:
        slots.release()
        raise
    t.join(max(deadline - time.monotonic(), 0))
    if t.is_alive():
        raise SearchTimeout()
    if "error" in got:
        raise got["error"]
    addrs = got["addrs"]
    last = None
    for family, kind, proto, _, addr in addrs:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(left)
            sock.connect(addr)
            return sock
        except OSError as e:
            sock.close()
            last = e
    if deadline - time.monotonic() <= 0:
        raise SearchTimeout()
    raise last or OSError(f"web search gateway {u.hostname} did not resolve")


def web_search(auth, query, n=8):
    """One AgentCore Web Search call through the web search MCP gateway, as the caller (their bearer token), so the
    device holds no search API key and the MCP-side gate meters the caller."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "web___WebSearch", "arguments": {"query": query, "maxResults": n}}}).encode()
    # MCP Streamable HTTP: a client must accept both, and the server may answer either way.
    headers = {"Authorization": auth, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    u = urllib.parse.urlsplit(WEBSEARCH_MCP_URL)
    target = (u.path or "/") + (f"?{u.query}" if u.query else "")

    # One wall-clock deadline over the whole call. The connect is bounded per address (search_connect); from then on a
    # watchdog timer shuts the socket when the time is up, which ends whatever http.client is waiting on (the TLS
    # handshake, the status line, a header, a chunk size line, a read), however slowly the bytes trickle in. The timer
    # is cancelled on the way out, so nothing runs after the caller has its answer.
    deadline = time.monotonic() + SEARCH_DEADLINE_S
    timed_out = threading.Event()
    conn = (http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection)(u.hostname, u.port)
    watchdog = wake = None
    try:
        conn.sock = sock = search_connect(u, deadline)  # closed with conn, whatever fails next
        wake = sock.dup()  # the same TCP connection; shutting it down wakes a read blocked on sock, with TLS on top too

        def expire():
            timed_out.set()
            try:
                wake.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        watchdog = threading.Timer(max(deadline - time.monotonic(), 0), expire)
        watchdog.daemon = True
        watchdog.start()
        if u.scheme == "https":
            conn.sock = tls_context().wrap_socket(sock, server_hostname=u.hostname)
        # http.client sends on the connection it is given (conn.sock) and does not connect again.
        conn.request("POST", target, body=body, headers=headers)
        r = conn.getresponse()
        ctype = (r.getheader("content-type") or "").split(";")[0].strip().lower()
        if ctype == "text/event-stream" and not 200 <= r.status < 300:
            # An MCP refusal comes as a JSON body; an error status on a stream is the gateway failing: no need to wait
            # for an event that may never come.
            raise RuntimeError(f"web search gateway answered HTTP {r.status}")
        if ctype == "text/event-stream":
            # A reply is seen as soon as it arrives, whatever its line endings; sse_lines rejoins a line split over
            # chunks, and SEARCH_REPLY_MAX caps the whole stream, not one line.
            reply = sse_reply(search_chunks(r, timed_out))
        else:
            raw = b"".join(search_chunks(r, timed_out))
            try:
                reply = json.loads(raw)
            except ValueError:
                reply = None
                if 200 <= r.status < 300:
                    raise
    except (OSError, http.client.HTTPException, ValueError):
        if timed_out.is_set() or deadline - time.monotonic() <= 0:  # the watchdog shut the socket, or a connect ran out
            raise SearchTimeout() from None
        raise
    finally:
        if watchdog:
            watchdog.cancel()
            watchdog.join()  # expire() has finished with wake before it is closed
        if wake:
            wake.close()
        conn.close()
    if not 200 <= r.status < 300:
        # A JSON-RPC error (an MCP refusal) is handled below whatever the status; anything else is the gateway failing.
        if not (isinstance(reply, dict) and isinstance(reply.get("error"), dict)):
            raise RuntimeError(f"web search gateway answered HTTP {r.status}")
    if not (isinstance(reply, dict) and ("result" in reply or "error" in reply)):
        # A list, {} or any other shape is not a JSON-RPC response: a gateway fault, not an empty search.
        raise RuntimeError("web search gateway reply is not a JSON-RPC response")
    err = reply.get("error")
    if isinstance(err, dict) and err.get("code") in SearchRefused.HTTP:
        raise SearchRefused(err["code"], str(err.get("message") or "web search refused")[:300])
    if err:
        code = err.get("code") if isinstance(err, dict) else ""
        raise RuntimeError(f"web search JSON-RPC error {code}")  # no reply text: it can hold the search (r195)
    res = reply.get("result")
    res = res if isinstance(res, dict) else {}  # a list or a string result: no hits, not a 502 after the charge
    if res.get("isError"):
        raise RuntimeError("web search tool returned isError")
    return search_results(res)


def search_results(res):
    """The hits of a Web Search tools/call result. The search ran (and was charged) by the time this runs, so a text
    that is not the expected JSON, or items of the wrong shape, give no results rather than a 502."""
    content = res.get("content") if isinstance(res.get("content"), list) else []
    text = next((c.get("text") for c in content if isinstance(c, dict) and c.get("type") == "text"), "{}")
    try:
        hits = json.loads(text).get("results", []) if isinstance(text, str) else []
    except (ValueError, AttributeError, RecursionError):  # RecursionError: a text nested too deep to parse
        print(json.dumps({"outcome": "websearch_unparsed", "chars": len(str(text))}))
        hits = []
    return [{"title": _text(h.get("title")) or _text(h.get("url")), "url": _text(h.get("url")) or None,
             "snippet": _text(h.get("text"))[:1500],
             "published": _text(h.get("publishedDate")) or None} for h in (hits if isinstance(hits, list) else []) if isinstance(h, dict)]


def _text(v):
    """A hit's field as a string: "" for none, anything else (a number, an object) as its JSON."""
    return v if isinstance(v, str) else ("" if v is None else json.dumps(v))


def normalise(body, model):
    """Request shapes opencode sends that open-weight models on Mantle reject. Returns the list of changes made."""
    changed = []
    for m in body.get("messages") or []:
        # Kimi K3 rejects an assistant turn whose content is "" when it carries tool calls (opencode #39451, #46577).
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls") and m.get("content") in ("", []):
            m["content"] = None
            changed.append("empty_assistant_content")
    if body.get("stream") is True and "messages" in body:
        so = body.get("stream_options") if isinstance(body.get("stream_options"), dict) else {}
        if not so.get("include_usage"):
            body["stream_options"] = {**so, "include_usage": True}  # usage in the last chunk, for logs and clients
            changed.append("include_usage")
    # usage_sync prices every token of a model at one on-demand rate, so a dearer tier would be under-counted (r194).
    if body.pop("service_tier", None) is not None:
        changed.append("service_tier_removed")
    # The gateway may answer from a fallback hop, so clamp to the smallest limit in the chain.
    caps = [c for c in (MAX_OUT.get(m) for m in [model, *FALLBACKS.get(model, [])]) if c]
    cap = min(caps) if caps else None
    for k in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
        v = body.get(k)
        if cap and isinstance(v, (int, float)) and not isinstance(v, bool) and v > cap:  # 50000.0 is a number too
            body[k] = cap
            changed.append(f"{k}_clamped")
    return changed


def needs_directory(event, mcp):
    """True unless this is an MCP call that never looks a user up (initialize, tools/list, notifications, stream GET
    or DELETE, which carry no body and go on with an empty one): only tools/call is checked against the user's
    project, budget and rpm. Anything else unclear (a body that is not one JSON-RPC object) reads it, so a check is
    never skipped by accident."""
    if not mcp:
        return True
    body = ((event.get("mcp") or {}).get("gatewayRequest") or {}).get("body")
    if body is None:
        return False
    return not isinstance(body, dict) or body.get("method") == "tools/call"


def lambda_handler(event, context):
    """Any crash fails closed with a generic error: the exception text (and Lambda's request id) stays in the log."""
    mcp = isinstance(event, dict) and "mcp" in event
    global PROJECTS, IDENTITY_MAP  # noqa: PLW0603 - refreshed from the ledger's CONFIG/DIRECTORY item (policy.directory)
    try:
        if os.environ.get("DIRECTORY_FROM_TABLE") == "1" and needs_directory(event, mcp):
            PROJECTS, IDENTITY_MAP = policy.directory(ddb, TABLE, os.environ)
        return mcp_handler(event) if mcp else http_handler(event)
    except Exception as e:  # noqa: BLE001 - fail closed; never forward a request that was not checked
        print(json.dumps({"outcome": "interceptor_error", "error": f"{type(e).__name__}: {e}"[:500]}))
        if mcp:
            body = ((event.get("mcp") or {}).get("gatewayRequest") or {}).get("body")
            return mcp_error(body.get("id") if isinstance(body, dict) else None, -32603, "the gateway could not check this request")
        return deny(500, "api_error", "the gateway could not check this request")


def http_method(req):
    """The method AgentCore reports (httpMethod, or method), for the log only. It is not used to route: on a live
    gateway it reads POST even for GET /inference/v1/models, so the path alone names the route."""
    m = req.get("httpMethod") or req.get("method")
    return str(m).upper() if m else None


def route_of(req):
    """(method, route) for a known route, else None. AgentCore hands over the path already decoded once (seen live:
    chat%2Fcompletions arrives as chat/completions), so a '%' still in it is double encoding and is refused, not
    decoded again; so is any path that only ends with a known route after extra segments. Each route has one method,
    so the path decides (see http_method)."""
    path = (req.get("path") or "").split("?")[0].rstrip("/")
    if "%" in path or "/../" in path + "/" or "//" in path:
        return None
    for m, r in ROUTES:
        if path == r or path == "/inference" + r:
            return m, r
    return None


def http_handler(event):
    """The HTTP gateway's interceptor (_http). On every exit it waits for a groups write it started (read["record"]),
    so the write never outlives the request (the 500 path never reaches finish)."""
    read = {}  # spend() leaves the BUDGET item here, limits() the groups write it started
    try:
        return _http(event, read)
    finally:  # every exit, a return or a raise: the write never outlives the request
        if "record" in read:
            read["record"].result()  # record_groups never raises


def _http(event, read):
    t0 = time.time()
    req = (event.get("http") or {}).get("gatewayRequest") or {}
    headers = {k: v for k, v in (req.get("headers") or {}).items() if k.lower() not in INJECTED}
    raw = req.get("body") or ""
    claims = claims_of(headers)
    raw_user = user_claim(claims)
    user, groups, iat = ledger_user(raw_user), groups_of(claims), token_iat(claims)
    log = {"user": user, "idp_sub": raw_user, "iss": claims.get("iss"), "groups": groups, "path": str(req.get("path"))[:300], "method": http_method(req)}
    def done(outcome, result, record=True):
        finish(log, outcome, t0, user, groups, iat, read, record)
        return result

    if not user:
        if raw_user:  # the claim is there, but identity_map does not name a ledger user for it
            return done("unmapped_user", deny(403, "permission_error", f"IdP user '{raw_user}' is not in this gateway's identity_map; ask your admin to add it."))
        return done("no_user_claim", deny(403, "permission_error", f"token has no '{USER_CLAIM}' claim"))
    route = route_of(req)
    if route is None:
        return done("bad_route", deny(404, "not_found_error", "unknown route: use /inference/v1/chat/completions, /inference/v1/responses or /inference/v1/models"))
    method, path = route
    if ddb and user not in PROJECTS:
        # One check for every route: without a Mantle project a call cannot be attributed or priced, a model list would
        # name models every call refuses, and a search would be charged to a ledger nobody seeded.
        return done("no_project", deny(403, "permission_error", NO_PROJECT.format(user=user)))
    try:
        body = json.loads(base64.b64decode(raw), parse_constant=_no_constant) if raw else {}
    except (ValueError, RecursionError):
        body = None
    if not isinstance(body, dict):
        # Fail closed: a body the interceptor cannot read must not reach a target unchecked (no model, no allow-list).
        return done("bad_body", deny(400, "invalid_request_error", "request body must be a JSON object"))
    if "messages" in body and not isinstance(body["messages"], list):  # a 400 here, not a crash in normalise (500)
        return done("bad_body", deny(400, "invalid_request_error", "messages must be a list"))
    try:  # refuse what cannot be forwarded (1e999 parses to inf) before any rate-limit slot or request count is taken
        json.dumps(body, allow_nan=False)
    except ValueError:
        return done("bad_number", deny(400, "invalid_request_error", "request body has a number JSON cannot represent"))

    def limits():
        """Budget and per-user rpm gate; returns a denial, or None when the request may go on."""
        if not ddb:
            return None
        windows, rpm, eff = spend(user, groups, read)
        # The groups write, when one may be needed, runs beside the gate's rpm write, not after it; finish() (or
        # http_handler, if anything raises) waits for it.
        if may_record(user, groups, iat, read.get("budget")):
            read["record"] = _POOL.submit(record_groups, user, groups, iat, read.get("budget"))
        spent, budget = windows["Monthly"]
        log.update(spent=round(spent, 6), budget=budget, day_spent=round(windows["Daily"][0], 6), hour_spent=round(windows["Hourly"][0], 6))
        if not (g := gate(user, groups, windows, rpm, eff)):
            return None
        outcome, message, extra = g
        log.update(extra)
        if outcome == "budget_exceeded":  # 402-style refusal as a 400 with a stable code: retrying does not help until the reset
            return done(outcome, deny(400, "invalid_request_error", message, "budget_exceeded"))
        return done(outcome, deny(429, "rate_limit_error", message))

    if path == "/v1/models":
        if "model" in body:
            return done("bad_models_body", deny(400, "invalid_request_error", "GET /v1/models takes no body"))
        listing = models_for(groups)
        log["models"] = len(listing["data"])
        return done("models_list", answer(200, listing))
    if path == "/websearch":
        if not WEBSEARCH_MCP_URL:
            return done("bad_route", deny(404, "not_found_error", "web search is not enabled"))
        q = body.get("q") or body.get("query") or ""
        # 0 is invalid, not "unset"; an explicit null is "not given", as JSON clients send for optional fields.
        count = next((body[k] for k in ("count", "maxResults") if body.get(k) is not None), 8)
        if not search_query_ok(q, count):
            return done("websearch_bad", deny(400, "invalid_request_error", "websearch needs {\"q\": \"...\"} and an optional count of 1-20"))
        if not usable_models(allow_list(groups)):
            return done("websearch_denied", deny(403, "permission_error", f"{user} has no model access, so web search is not allowed."))
        # Budget, rpm and the charge are applied once, by the web search gateway's interceptor (mcp_handler) on the
        # call below; checking them here as well would take two rpm slots per search.
        auth = next((v for k, v in (req.get("headers") or {}).items() if k.lower() == "authorization"), "")
        try:
            results = web_search(auth, q.strip(), count)
        except SearchRefused as e:
            status, etype, code = SearchRefused.HTTP[e.code]
            return done("websearch_refused", deny(status, etype, e.message, code), record=False)  # MCP leg recorded
        except Exception as e:  # noqa: BLE001 - reported to the client, logged here
            log["error"] = str(e)[:300]
            return done("websearch_error", deny(502, "api_error", "web search failed"))
        log["results"] = len(results)
        return done("websearch", answer(200, {"query": q.strip(), "results": results}), record=False)  # MCP leg recorded

    # chat/completions or responses: a model is required.
    model = body.get("model")
    if not isinstance(model, str) or not model:
        return done("no_model", deny(400, "invalid_request_error", "model must be a non-empty string"))
    alias = None
    if model in MODEL_ALIASES:
        alias, model = model, MODEL_ALIASES[model]
    s = body.get("stream")
    log.update(model=str(model)[:200], alias=alias, stream=s if s is None or isinstance(s, bool) else "not_a_boolean")
    if model not in MANTLE_IDS:
        return done("unknown_model", deny(404, "not_found_error", f"Model '{alias or model}' is not served by this gateway."))
    # The allow-list is resolved here, before any ledger read: a model the caller may not use is refused with no
    # spend or rpm read (finish records the groups); spend() resolves the same groups again for the caps,
    # from the same cached policy items.
    patterns = allow_list(groups)
    if not policy.model_allowed(model, patterns):
        return done("model_denied", deny(403, "permission_error",
                                         f"Model '{model}' is not allowed for {user} (groups: {', '.join(groups) or 'none'})."))
    # The gateway may answer from a fallback hop after this check. Terraform checks group_models against the chains,
    # but dashboard edits can change a group's models later, so check the chain here against what the caller may use.
    if barred := barred_hops(model, patterns):
        return done("fallback_denied", deny(403, "permission_error",
                                            f"Model '{model}' can fall back to {', '.join(barred)}, which {user} may not use. "
                                            "Ask your admin to allow the fallback model too, or pick another model."))
    denied = limits()
    if denied is not None:
        return denied
    changes = normalise(body, model)
    if changes:
        log["normalised"] = changes
    body["model"] = MANTLE_IDS[model]
    count_request(user, model, read.get("at", time.time()))  # on _BG, never delays the call; in the windows gated
    if user in PROJECTS:
        headers["OpenAI-Project"] = PROJECTS[user]
    headers["x-ocgw-user"] = user
    # The body below is re-serialised and so has a different length: never pass the client's framing headers on.
    headers = {k: v for k, v in headers.items() if k.lower() not in ("content-length", "transfer-encoding")}
    # Forward the body as parsed: with duplicate keys ({"model": a, ..., "model": b}) Python keeps b, and a downstream
    # parser that kept a would serve a model this check never saw. Numbers JSON cannot hold were refused on entry.
    raw = base64.b64encode(json.dumps(body, allow_nan=False).encode()).decode()
    return done("allow", {"interceptorOutputVersion": "1.0", "http": {"transformedGatewayRequest": {"headers": headers, "body": raw}}})


def gate(user, groups, windows, rpm, eff):
    """The budget and per-user rpm gate shared by model calls (http_handler) and web search (mcp_handler).
    Returns None to go on, or (outcome, message, log fields); each caller maps the outcome to its own protocol."""
    if over := over_budget(windows):
        w, s_, cap, readable = over
        if not readable:
            return ("budget_exceeded", f"{w} spend for {user} cannot be read, so the {w.lower()} budget is treated as "
                    "used up; ask your admin to check the usage record.", {"window": w, "unreadable": True})
        return ("budget_exceeded", f"{w} budget exceeded for {user}: spent ${s_:.4f} of ${cap:.2f}. It resets {RESETS[w]}; "
                + (eff.get("block_message") or "ask your admin to raise it."), {"window": w})
    if rpm is not None and not take_rpm(user, rpm):
        return ("rate_limited", f"Rate limit for {user}: {rpm} requests per minute across all models and web search. "
                "Try again in a minute.", {"rpm": rpm})
    return None


def search_query_ok(q, count):
    """The one check for a web search request, on /websearch and on an MCP tools/call alike: a non-empty query of at
    most 2,000 characters and an integer count of 1-20. Checked before any charge or rpm slot."""
    return (isinstance(q, str) and 0 < len(q.strip()) <= 2000 and not isinstance(count, bool) and isinstance(count, int)
            and 1 <= count <= 20)


def mcp_error(rid, code, message):
    """The interceptor's answer that refuses an MCP call: a JSON-RPC error the gateway returns to the client as is."""
    return {"interceptorOutputVersion": "1.0", "mcp": {"transformedGatewayResponse": {"statusCode": 200, "body": {
        "jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}}}}


def mcp_handler(event):
    """Web search MCP gateway (websearch.tf): a tools/call needs access to some model, counts against the caller's
    rpm, is refused once over budget (the same gate as a model call) and adds WEBSEARCH_USD to the caller's spend.
    Other MCP methods (initialize, tools/list) pass. MCP interceptor events carry the body as JSON, not base64."""
    t0 = time.time()
    req = event["mcp"].get("gatewayRequest") or {}
    body = req.get("body")
    if body is None:
        # No body the interceptor can read: an MCP Streamable HTTP GET (open the stream) or DELETE (end the session),
        # or a request whose body the gateway did not parse. It goes on with an empty body, never as it came: what
        # cannot be read here is never forwarded (a tools/call hidden in it would skip the gates below). Fail closed.
        return {"interceptorOutputVersion": "1.0", "mcp": {"transformedGatewayRequest": {"body": {}}}}
    if not isinstance(body, dict):
        # A JSON-RPC batch (a list) or any other shape: refused plainly, never forwarded as something else (a batch
        # could also carry a tools/call past the per-call gate below).
        print(json.dumps({"mcp": "not_an_object", "outcome": "mcp_bad_body"}))
        return mcp_error(None, -32600, "send one JSON-RPC request object (batches are not supported)")
    passthrough = {"interceptorOutputVersion": "1.0", "mcp": {"transformedGatewayRequest": {"body": body}}}
    if body.get("method") != "tools/call":
        return passthrough
    claims = claims_of(req.get("headers") or {})
    raw_user = user_claim(claims)
    user, groups, iat = ledger_user(raw_user), groups_of(claims), token_iat(claims)
    params = body.get("params") if isinstance(body.get("params"), dict) else {}
    log = {"user": user, "groups": groups, "mcp": "tools/call", "tool": str(params.get("name"))[:200]}

    read = {}  # spend() leaves the BUDGET item here for record_groups

    def refuse(outcome, code, message):
        finish(log, outcome, t0, user, groups, iat, read)
        return mcp_error(body.get("id"), code, message)

    if not user:
        if raw_user:
            return refuse("unmapped_user", -32001, f"IdP user '{raw_user}' is not in this gateway's identity_map")
        return refuse("no_user_claim", -32001, f"token has no '{USER_CLAIM}' claim")
    # Only the web search target's tools are gated and charged here; refuse any other tool rather than pass it unchecked.
    if not str(params.get("name") or "").startswith("web___"):
        return refuse("tool_denied", -32004, "only the web search tools are available on this gateway")
    args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    for k in [k for k, v in args.items() if v is None]:
        del args[k]  # null is "not given", as on the HTTP route; dropped in place, as this dict is forwarded
    if not search_query_ok(args.get("query"), args.get("maxResults", 8)):
        return refuse("websearch_bad", -32602, "web search needs a query of 1-2000 characters and maxResults of 1-20")
    if ddb and user not in PROJECTS:  # as for every HTTP route: checked before spend(), so it costs no ledger read
        return refuse("no_project", -32004, NO_PROJECT.format(user=user))
    # Search is billed to the account: only users who can call some model may run it. Models come from group policy
    # only (cached POLICY items), so as on the HTTP routes this refusal costs no ledger read.
    if not usable_models(allow_list(groups)):
        return refuse("websearch_denied", -32004, f"{user} has no model access (groups: {', '.join(groups) or 'none'}), so web search is not allowed.")
    if ddb:
        windows, rpm, eff = spend(user, groups, read)
        if g := gate(user, groups, windows, rpm, eff):
            outcome, message, extra = g
            log.update(extra)
            return refuse(outcome, -32002 if outcome == "budget_exceeded" else -32003, message)
        try:
            charge_search(user, read.get("at"))
        except Exception as e:  # noqa: BLE001 - budget and rpm were already checked; record the miss, run the search
            # Failing here would cost the user an rpm slot and possibly a partial charge for a search that never ran.
            # One unrecorded $0.007 is the smaller error, and the log line lets an admin see it.
            log["charge_error"] = f"{type(e).__name__}: {e}"[:200]
    finish(log, "mcp_tool_call", t0, user, groups, iat, read)
    return passthrough


def charge_search(user, at=None):
    """Price one search (WEBSEARCH_USD, $7 per 1,000 queries) into the user's MONTH/DAY/HOUR spend before it runs, so
    search counts against the budget and shows on the dashboard like model spend."""
    add_to_windows(user, "spend_usd :c, websearch_requests :one", None, {":c": {"N": repr(WEBSEARCH_USD)}, ":one": {"N": "1"}},
                   at=at)  # at: when spend() picked the windows the gate checked