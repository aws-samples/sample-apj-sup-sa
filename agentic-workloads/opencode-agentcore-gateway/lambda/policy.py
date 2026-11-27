# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Limits and model access, resolved from the ledger table's policy items (shared by the interceptor and the dashboard).

Items (all optional; with none, the Lambda env defaults apply exactly as before):
  pk POLICY  sk ORG            monthly_usd, daily_usd, hourly_usd, rpm, warn_pct, block_message
  pk POLICY  sk GROUP#<group>  same caps (each optional) + models (list of IDs or "prefix*")
  pk USER#u  sk BUDGET         user override: budget_usd (monthly), daily_usd, hourly_usd, rpm,
                               boost_usd + boost_period (Monthly|Daily|Hourly) + boost_until (epoch s) + boost_reason

Per limit: user override, else the most generous of the user's groups that set it, else the org item, else env.
By design a group that sets a limit replaces the org/env default for its members: a user in a capped group and in a
group that sets nothing gets the capped group's limit, even when the default is higher. Set the limit on every group
(or on the user) where a member should keep the higher figure.
A boost adds to the resolved cap of its period until boost_until, when that cap is above 0 (a $0 cap is a block). Models: the union of the user's groups' lists, where
a group without `models` in DynamoDB keeps its env list (GROUP_MODELS).
"""
import json
import math
import time

PERIODS = {"Monthly": "monthly_usd", "Daily": "daily_usd", "Hourly": "hourly_usd"}
PERIOD_KIND = {"Monthly": "MONTH", "Daily": "DAY", "Hourly": "HOUR"}  # each period's ledger window (window_sk)
USER_KEYS = {"Monthly": "budget_usd", "Daily": "daily_usd", "Hourly": "hourly_usd"}  # BUDGET item names (legacy)
CACHE_SECONDS = 60


def env_limits(env):
    """Deployment defaults from the Lambda environment, read the same way by the interceptor and the dashboard:
    DEFAULT_BUDGET_USD (monthly; $50 when unset, so a missing setting never means unlimited), DEFAULT_DAILY_USD,
    DEFAULT_HOURLY_USD and DEFAULT_RPM (no cap when unset)."""
    num = lambda k: _limit(env[k]) if env.get(k) else None  # noqa: E731 - the item rule: a bad value blocks, never crashes
    return {"Monthly": num("DEFAULT_BUDGET_USD") if env.get("DEFAULT_BUDGET_USD") else 50.0, "Daily": num("DEFAULT_DAILY_USD"),
            "Hourly": num("DEFAULT_HOURLY_USD"), "rpm": rpm_whole(num("DEFAULT_RPM"))}
# gen: bumped by clear_cache(). A read started before a clear (a pooled read still running when a request ended, then
# thawed in the next invocation) returns its items to its own caller but never writes them to the cache.
_cache = {"at": 0.0, "items": {}, "gen": 0}


NO_GROUP = "(no group)"  # the GROUP# bucket for users whose token carries no groups


def tag_dims(env):
    """The directory tag dimensions rolled up as TAG# rows (Terraform var.tag_dimensions as TAG_DIMENSIONS). An empty
    value means tags are off, for the usage sync and the dashboard alike."""
    return [d.strip() for d in env.get("TAG_DIMENSIONS", "team,project,environment").split(",") if d.strip()]


SPEND_ATTRS = ("model_spend_usd", "spend_usd")  # usage_sync SETs model spend; the interceptor ADDs web search spend


def amount(item, name):
    """(value, readable) of one money attribute of a raw item: a DynamoDB number that is finite and not negative is
    (value, True); absent is (0.0, True); anything else (a hand edit) is (0.0, False). The one parse of a spend amount,
    so the gate, the dashboard and the rollups agree on what a bad edit is worth: nothing, and never a negative."""
    v = number(item, name)
    return (v, True) if v is not None and math.isfinite(v) and v >= 0 else (0.0, False)


def window_read(item):
    """(spend, readable) of a window item in one pass: the sum the interceptor caps on and the dashboard shows
    (always finite: a bad attribute adds 0), and whether every spend attribute could be trusted. A window that is not
    readable is refused at the gate and shown blocked where it has a cap, and flagged on the dashboard everywhere."""
    parts = [amount(item, a) for a in SPEND_ATTRS]
    return sum(v for v, _ in parts), all(ok for _, ok in parts)


def window_spend(item):
    """window_read's sum alone, for totals and display."""
    return window_read(item)[0]


def number(item, name):
    """A number attribute of a raw item read strictly: 0.0 when the item or attribute is absent, None when it is not a
    DynamoDB number (a hand edit). The one strict parse that amount() and the groups_iat check build on."""
    try:
        return num_attr(item, name, strict=True)
    except ValueError:
        return None


def bad_attrs(item):
    """The spend attributes of a window item that window_read cannot trust (not a number, or negative): what the
    dashboard's repair removes. Empty for a readable item."""
    return [a for a in SPEND_ATTRS if not amount(item, a)[1]]


def refused(readable, spent, cap):
    """True when the gate refuses on a window: the one rule the interceptor applies (over_budget) and the dashboard
    shows as blocked. An unreadable record refuses only where there is a cap (a window with no cap limits nothing,
    r141); otherwise spend at the cap refuses (at_cap)."""
    return cap is not None and (not readable or at_cap(spent, cap))


def at_cap(spent, cap):
    """True when spend has reached the cap: the one rule the interceptor refuses on and the dashboard shows as blocked.
    Spend is a sum of floats (model_spend_usd + spend_usd), so a total that is exactly the cap in decimal can come out a
    hair under it; compare at a micro-dollar so "at the cap" is refused every time."""
    return cap is not None and spent >= cap - 1e-6


# How long each ledger window item is kept (DynamoDB ttl), for every Lambda that writes one.
WINDOW_TTL = {"MONTH": 400 * 86400, "DAY": 40 * 86400, "HOUR": 3 * 86400}
# The one format of each window's sort key (MONTH#2026-11, DAY#2026-11-12, HOUR#2026-11-12T09). usage_sync writes the
# spend on these keys and the interceptor and dashboard read it back, so all three build them with window_sk.
WINDOW_FMT = {"MONTH": "%Y-%m", "DAY": "%Y-%m-%d", "HOUR": "%Y-%m-%dT%H"}


def window_sk(kind, when):
    """Sort key of the kind window (MONTH, DAY or HOUR) holding when: a UTC datetime or a time.gmtime() struct."""
    fmt = WINDOW_FMT[kind]
    return f"{kind}#{when.strftime(fmt) if hasattr(when, 'strftime') else time.strftime(fmt, when)}"


def batch_get(ddb, table, keys, attempts=8, base=0.1, workers=8, consistent=False):
    """{(pk, sk): item} for many keys, 100 per BatchGetItem, up to `workers` chunks at a time. Keys DynamoDB returns as
    UnprocessedKeys (throttling) are retried with backoff; if some never come back this raises, because a missing
    ledger item would read as "nothing spent, no budget, no groups" and let a call through (fail closed)."""
    todo = list(dict.fromkeys(keys))

    def chunk(part):
        got, req = {}, {table: {"Keys": [{"pk": {"S": pk}, "sk": {"S": sk}} for pk, sk in part], "ConsistentRead": consistent}}
        for attempt in range(attempts):
            r = ddb.batch_get_item(RequestItems=req)
            for it in r.get("Responses", {}).get(table, []):
                got[(it["pk"]["S"], it["sk"]["S"])] = it
            req = r.get("UnprocessedKeys") or {}
            if not req:
                return got
            if attempt < attempts - 1:
                time.sleep(base * 2 ** attempt)
        raise RuntimeError(f"BatchGetItem left {len(req[table]['Keys'])} keys unprocessed after {attempts} attempts")
    parts = [todo[i:i + 100] for i in range(0, len(todo), 100)]
    if len(parts) <= 1 or workers <= 1:
        results = [chunk(p) for p in parts]
    else:  # independent chunks: the round trips overlap (usage_sync reads several keys per user every run)
        import concurrent.futures  # noqa: PLC0415
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(parts))) as ex:
            results = list(ex.map(chunk, parts))
    return {k: v for r in results for k, v in r.items()}


def num_attr(item, name, strict=False):
    """A number attribute of a raw DynamoDB item, 0.0 when the item or attribute is absent: the one read every Lambda
    uses. An unreadable value (a hand edit) is 0.0 for display, but raises with strict=True: a check that limits
    something reads strictly, so a bad edit blocks and never lifts a limit (as limits() treats caps)."""
    try:
        return float(item[name]["N"]) if item and name in item else 0.0
    except (KeyError, TypeError, ValueError):
        if strict:
            raise ValueError(f"{name} is not a DynamoDB number") from None
        return 0.0


def token_groups(item):
    """The groups a raw BUDGET item records from the user's last token (token_groups): non-blank strings, None when it
    records none. The one parse of the attribute, shared by budget_groups and the interceptor's groups record."""
    if not item or "token_groups" not in item or not isinstance(item["token_groups"], dict):
        return None
    return [x["S"] for x in (item["token_groups"].get("L") or []) if isinstance(x, dict) and isinstance(x.get("S"), str) and x["S"]]


def budget_groups(item):
    """A user's groups from their raw BUDGET item, the one rule the dashboard and the usage sync share: the groups the
    interceptor last saw on the user's token (token_groups, an empty list included, which is what it applies), else
    the group Terraform seeded (a user who has not called yet), else none. Sorted, without blanks."""
    item = item or {}
    if (recorded := token_groups(item)) is not None:
        gs = set(recorded)
    else:
        gs = {item.get("group", {}).get("S")} - {None, ""}
    return sorted(gs)


# Numeric fields of POLICY and BUDGET items, cleaned by limits() for every reader (interceptor, dashboard, usage_sync).
LIMIT_FIELDS = frozenset({"monthly_usd", "budget_usd", "daily_usd", "hourly_usd", "rpm", "boost_usd", "boost_until"})
BOOST_FIELDS = ("boost_usd", "boost_period", "boost_until", "boost_reason")
BOOST_UNTIL_MAX = 32503680000  # 3000-01-01: later is no real boost end, and time.gmtime cannot format it everywhere


def limits(item):
    """A POLICY or BUDGET item's values (already plain) with hand-written ones made safe, the one rule every reader
    uses: each LIMIT_FIELDS value through _limit (a number; "inf", "nan", words and negatives block, never lift); a
    boost that is a hand edit (_boost_hand_edit: a bad amount, end or period, a reason that is not text, or a boost that is
    not whole: an amount without an end, an end without an amount, or a period or reason with neither) is no boost,
    for the gate and the dashboard alike: its amount reads as 0."""
    out = dict(item or {})
    for k in LIMIT_FIELDS & out.keys():
        out[k] = _limit(out[k])
    if out.get("rpm") is not None:  # requests a minute are whole: what the gate enforces is what every view shows
        out["rpm"] = rpm_whole(out["rpm"])
    if (out.get("boost_until") or 0) > BOOST_UNTIL_MAX:
        out["boost_until"] = 0.0
    if "boost_period" in out and not (isinstance(out["boost_period"], str) and out["boost_period"] in PERIODS):
        out["boost_period"] = "Monthly"
    if _boost_hand_edit(item or {}, out):
        out["boost_usd"] = 0.0
    return out


def plain(item):
    """DynamoDB attribute map -> Python values (N -> float, S, BOOL, L, M, SS, B and BS as bytes). Read POLICY and BUDGET items with
    limits(plain(item)), so hand-written values are cleaned."""
    def v(a):
        t, x = next(iter(a.items()))
        if t == "N":
            return float(x)
        if t == "L":
            return [v(e) for e in x]
        if t == "M":
            return {k: v(e) for k, e in x.items()}
        if t == "BS":
            return list(x)
        return None if t == "NULL" else x
    return {k: v(a) for k, a in (item or {}).items()}


BINARY_SHOWN = "<B>"  # how every reply and audit row shows a hand-written binary value


def shown(o):
    """json.dumps default for a reply: a hand-written binary as "<B>"; anything else JSON cannot hold is a bug, and
    raises as json.dumps would."""
    if isinstance(o, (bytes, bytearray)):
        return BINARY_SHOWN
    raise TypeError(f"{type(o).__name__} is not JSON serializable")


def shown_text(o):
    """json.dumps default for an audit row, which must never fail the write it records: shown(), else the value as text."""
    try:
        return shown(o)
    except TypeError:
        return str(o)


def _same(a, b):
    """a == b for JSON values, except that a bool is never a number (JSON keeps them apart; Python's True == 1 does not)
    and numbers compare as the doubles a JSON client holds (a client sends 1.2345678901234567e19 back as
    12345678901234567000)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        try:
            return float(a) == float(b)
        except OverflowError:  # an int past any double: no JSON client holds it, and a stored N past one shows as no reply could
            return False
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def version_matches(stored_av, sent):
    """Whether the version a client sent back is the stored one (a raw DynamoDB attribute value). The stored value is
    rendered exactly as every reply renders it (plain(), then JSON with shown()), so whatever a client was shown, at any
    depth, it can send back: a hand-written binary as "<B>", a number as the double the reply held."""
    try:
        as_shown = json.loads(json.dumps(plain({"v": stored_av})["v"], allow_nan=False, default=shown))
    except (TypeError, ValueError):  # a value no reply can show (NaN): no client holds it
        return False
    return _same(as_shown, sent)


def read_item(item):
    """A POLICY or BUDGET item from DynamoDB, ready to use: plain values, hand-written ones cleaned (limits)."""
    return limits(plain(item))


def paginate(ddb, op, **kw):
    """Every item of a Query or Scan (op = "query" or "scan"), following LastEvaluatedKey: the one pagination loop the
    Lambdas share."""
    while True:
        r = getattr(ddb, op)(**kw)
        yield from r.get("Items", [])
        if "LastEvaluatedKey" not in r:
            return
        kw = {**kw, "ExclusiveStartKey": r["LastEvaluatedKey"]}


def paginate_token(call, token_key, list_keys, **kw):
    """Every item of a list call that pages with a token (CloudWatch NextToken, AgentCore nextToken): items under the
    first of list_keys that is non-empty (null and [] are empty). A token seen before (a paging fault) raises rather than
    loop until the Lambda times out. The one copy of the loop for the Lambdas."""
    seen = set()
    while True:
        r = call(**kw)
        yield from next((r[k] for k in list_keys if r.get(k)), [])  # the first non-empty list key
        token = r.get(token_key)
        if not token:
            return
        if token in seen:
            raise RuntimeError(f"{getattr(call, '__name__', 'list')}: the service repeated {token_key} {token!r}")
        seen.add(token)
        kw = {**kw, token_key: token}


def by_sk(ddb, table, index, sk, prefix=None):
    """Every item with this sort key (and a pk starting with prefix, if given): a Query on the sk index when there is
    one (ledger.tf by_sk), else a filtered Scan. The one copy of this rule for the dashboard and the usage sync."""
    cond, vals = "sk = :s", {":s": {"S": sk}}
    if prefix:
        cond += " AND begins_with(pk, :p)"
        vals[":p"] = {"S": prefix}
    if index:
        return paginate(ddb, "query", TableName=table, IndexName=index, KeyConditionExpression=cond, ExpressionAttributeValues=vals)
    return paginate(ddb, "scan", TableName=table, FilterExpression=cond, ExpressionAttributeValues=vals)


def policy_items(ddb, table, now=None):
    """All POLICY items {sk: attrs}, cached for CACHE_SECONDS per Lambda container."""
    now = now or time.time()
    if now - _cache["at"] > CACHE_SECONDS:
        gen = _cache["gen"]
        items = {it["sk"]["S"]: read_item(it) for it in paginate(
            ddb, "query", TableName=table, KeyConditionExpression="pk = :p", ExpressionAttributeValues={":p": {"S": "POLICY"}},
            ConsistentRead=True)}  # the dashboard sends these versions back as its lock right after a save
        if _cache["gen"] == gen:
            _cache.update(at=now, items=items)
        return items
    return _cache["items"]


def clear_cache():
    _cache.update(at=0.0, items={}, gen=_cache["gen"] + 1)


WARN_PCT = [75, 95]  # default warn and critical levels, % of a cap


def warn_pct(org):
    """The ORG item's warn and critical levels when they are valid (two ascending numbers from 1 to 100, as the
    dashboard saves them), else the default: an item written by hand must not break every view that shows a limit."""
    w = (org or {}).get("warn_pct")
    return [float(w[0]), float(w[1])] if valid_warn_pct(w) else list(WARN_PCT)


def valid_warn_pct(w):
    """Two ascending numbers from 1 to 100 (not booleans): the rule for saving warn_pct and for reading it back."""
    if not (isinstance(w, list) and len(w) == 2):
        return False
    return all(isinstance(x, (int, float)) and not isinstance(x, bool) and 1 <= x <= 100 for x in w) and w[0] < w[1]


DIRECTORY_KEY = {"pk": {"S": "CONFIG"}, "sk": {"S": "DIRECTORY"}}
_directory = {"at": 0.0, "tried": 0.0, "val": None}  # tried: the last read attempt, ok or not (backoff)


FIRST_RETRY_SECONDS = 2  # with no copy yet, how soon a failed directory read is tried again
STALE_SECONDS = 600  # how long a directory copy may serve while the ledger cannot be read


def directory(ddb, table, env, now=None):
    """(projects, identity_map): ledger user -> Mantle project id, and IdP user id -> ledger user. Terraform writes
    them to the ledger's CONFIG/DIRECTORY item (DIRECTORY_FROM_TABLE=1), because Lambda caps all environment variables
    at 4 KB together, which a per-user map passes at about 40 users. Without that flag they come from the PROJECTS
    and IDENTITY_MAP variables (tests, local runs); with neither, it raises. Cached for CACHE_SECONDS. A failed read,
    or the item missing or malformed, keeps serving a copy read within STALE_SECONDS (10 minutes), then raises, so
    callers fail closed; with no copy it raises at once. To cut users off, change the item; do not delete it.
    """
    if env.get("DIRECTORY_FROM_TABLE") != "1":
        if "PROJECTS" not in env and "IDENTITY_MAP" not in env:  # no source at all: everyone unknown, so stop
            raise RuntimeError("no user directory: set DIRECTORY_FROM_TABLE=1 (Terraform does) or PROJECTS/IDENTITY_MAP")
        return json.loads(env.get("PROJECTS") or "{}"), json.loads(env.get("IDENTITY_MAP") or "{}")
    now = now or time.time()
    # With a copy it may still serve, a failed read waits CACHE_SECONDS; with none (or one past STALE_SECONDS, which
    # cannot serve), every request fails anyway, so retry after FIRST_RETRY_SECONDS.
    servable = _directory["val"] is not None and now - _directory["at"] <= STALE_SECONDS
    wait = CACHE_SECONDS if servable else FIRST_RETRY_SECONDS
    retry_due = now - _directory.get("tried", 0) > wait
    # One rule for when to read: when the copy is missing or old and no read failed within the wait above (so a
    # throttled table is not asked again on every call).
    if retry_due and (_directory["val"] is None or now - _directory["at"] > CACHE_SECONDS):
        try:
            it = ddb.get_item(TableName=table, Key=DIRECTORY_KEY).get("Item")
            if not it:
                raise RuntimeError("CONFIG/DIRECTORY item is missing from the ledger (run terraform apply)")
            val = (json.loads(it["projects"]["S"]), json.loads(it["identity_map"]["S"]))
            if not all(isinstance(v, dict) for v in val):  # valid JSON of the wrong shape is malformed too
                raise ValueError("CONFIG/DIRECTORY projects and identity_map must be JSON objects")
        except Exception:
            # A failed read (a throttle, or the item missing or malformed for a moment) keeps a copy read within
            # STALE_SECONDS rather than failing every request, and the next read waits CACHE_SECONDS, so the table is
            # not asked again on every call.
            _directory["tried"] = now
            if servable:  # the copy (unchanged since the check above) may still serve
                return _directory["val"]
            raise
        _directory.update(at=now, tried=now, val=val)
    if _directory["val"] is None:  # never read, and the last try failed moments ago
        raise RuntimeError("the user directory could not be read; retrying after FIRST_RETRY_SECONDS")
    if now - _directory["at"] > STALE_SECONDS:  # retries backed off past the stale window: fail closed
        raise RuntimeError("the user directory could not be read for longer than STALE_SECONDS")
    return _directory["val"]


def groups_of(claims, claim):
    """The user's groups from a token's or the ALB's claims, read from the one configured claim (var.group_claim:
    groups, memberOf, cognito:groups) and nothing else, so the gateway and the dashboard always agree. A string is
    split on commas only: splitting on spaces would turn a group named "former ocgw-team-b" into ocgw-team-b."""
    g = (claims or {}).get(claim) or []
    if isinstance(g, str):
        g = g.split(",")
    return sorted({x.strip() for x in g if isinstance(x, str) and x.strip()}) if isinstance(g, list) else []


def model_attr(model, counter):
    """The ledger attribute for one per-model counter on a window item: m|<model>|<counter> (counter: req, usd, in,
    out). The interceptor, usage_sync and the dashboard all build and read it through these two functions; the
    interceptor's IAM attribute allow-list in terraform/policy.tf names the same m|*|req form."""
    return f"m|{model}|{counter}"


def parse_model_attr(name):
    """(model, counter) for an m|<model>|<counter> attribute name, else None. The counter is after the last '|'."""
    if not name.startswith("m|") or name.count("|") < 2:
        return None
    model, _, counter = name[2:].rpartition("|")
    return (model, counter) if model else None


def _boost_hand_edit(p, c):
    """True when p (plain values) holds a boost the dashboard never writes, c being p with each value cleaned: an amount
    or end that is not a finite number (a numeric string is a number), a negative amount, an end past BOOST_UNTIL_MAX,
    a period that is not a period, a reason that is not text, or a boost that is not whole (any boost attribute without
    an amount above 0 and an end). The dashboard writes all four; a period or reason left out reads as Monthly and no
    reason, as the gate always has. limits() makes
    such a boost no boost (its amount reads 0), so a boost attribute with no amount after limits() is a hand edit."""
    for a in ("boost_usd", "boost_until"):
        if a in p:
            try:
                v = None if isinstance(p[a], bool) else float(p[a])
            except (TypeError, ValueError):
                v = None
            if v is None or not math.isfinite(v) or v != c.get(a):
                return True
    if "boost_period" in p and p["boost_period"] != c.get("boost_period"):
        return True
    if "boost_reason" in p and not isinstance(p["boost_reason"], str):
        return True
    return any(a in p for a in BOOST_FIELDS) and not (c.get("boost_usd") and c.get("boost_until"))


def boost_live(budget, now=None):
    """True while a plain BUDGET item's boost is in force (boost_usd set and boost_until still ahead)."""
    # Read as _limit does, so a hand-written "inf", "nan" or word never lifts a cap and a string time never crashes.
    b = limits(budget)
    return bool(b.get("boost_usd")) and (b.get("boost_until") or 0) > (now or time.time())


def _limit(v):
    """A cap or rpm as stored (a number), or a hand-written item's value: a numeric string is read as its number,
    anything else (a list, a word, a bool) counts as 0, which blocks, so a bad edit fails closed, never open."""
    if isinstance(v, bool):
        return 0.0
    if v is None:
        return None
    try:
        f = float(v) if isinstance(v, (int, float, str)) else 0.0
    except ValueError:
        return 0.0
    return f if math.isfinite(f) and f >= 0 else 0.0  # "nan", "inf" or a negative never lifts or breaks a limit


def _patterns(v):
    """A group's model patterns: a list of strings. A hand-written single string is one pattern (not one per
    character, where a lone '*' would allow every model); any other shape allows nothing."""
    if isinstance(v, str):
        return [v]
    return [p for p in v if isinstance(p, str) and p] if isinstance(v, list) else []


def group_models(group_policies, groups, group_models_env):
    """The model patterns of the user's groups, in order without repeats, and where each group's came from: POLICY
    GROUP#<g>.models when set, else GROUP_MODELS from env. group_policies: {group: cleaned limits} (resolve's gp)."""
    models, msrc = [], {}
    for g in groups:
        set_in_ddb = group_policies.get(g, {}).get("models") is not None
        pats = _patterns(group_policies[g]["models"]) if set_in_ddb else group_models_env.get(g, [])
        msrc[g] = "dynamodb" if set_in_ddb else "env"
        models += [p for p in pats if p not in models]
    return models, msrc


def resolve(user_budget, policies, groups, env, group_models_env, now=None):
    """Effective limits for one user.

    user_budget: plain BUDGET item (or {}); policies: {sk: attrs} from policy_items; groups: the user's groups;
    env: {"Monthly": x, "Daily": x, "Hourly": x, "rpm": x} defaults (None = no cap);
    group_models_env: {group: [patterns]} from the GROUP_MODELS env.
    Returns {"caps": {period: {"cap" (enforced, boost included), "base" (before the boost), "source", "boost"}}, "rpm": {"value", "source"}, "models": [...], "models_source": {...}}.
    """
    now = now or time.time()
    user_budget = limits(user_budget)  # the one cleaning step; the reads below take the values as they are
    org = limits(policies.get("ORG", {}))
    gp = {g: limits(policies.get(f"GROUP#{g}", {})) for g in groups}

    def pick(user_key, policy_key, env_value):
        if user_budget.get(user_key) is not None:
            return user_budget[user_key], "user"
        vals = [(p[policy_key], f"group:{g}") for g, p in gp.items() if p.get(policy_key) is not None]
        if vals:
            return max(vals, key=lambda t: t[0])
        if org.get(policy_key) is not None:
            return org[policy_key], "org"
        return env_value, ("default" if env_value is not None else None)

    caps = {}
    live = boost_live(user_budget, now)
    for period, key in PERIODS.items():
        cap, src = pick(USER_KEYS[period], key, env.get(period))
        # A boost raises a cap above 0. On a period with no cap (removed after the grant) or a $0 cap (a block, from the
        # user, a group or the org, set before or after the grant) it is held back: 0 here, and the dashboard marks it
        # as having no effect. One rule for every source: it applies again if a cap above 0 is set before it expires,
        # and End boost removes it for good.
        lifts = live and cap is not None and cap > 0 and user_budget.get("boost_period", "Monthly") == period
        boost = float(user_budget["boost_usd"]) if lifts else 0.0
        caps[period] = {"cap": (cap + boost) if cap is not None else None, "base": cap, "source": src, "boost": boost}
    rpm, rsrc = pick("rpm", "rpm", env.get("rpm"))

    models, msrc = group_models(gp, groups, group_models_env)
    return {"caps": caps, "rpm": {"value": rpm, "source": rsrc},  # whole already: limits() and env_limits() round
            "models": models, "models_source": msrc, "warn_pct": warn_pct(org),
            "block_message": org["block_message"] if isinstance(org.get("block_message"), str) else None}


def rpm_whole(rpm):
    """Requests a minute as a whole number. The dashboard writes whole numbers; a hand-edited fraction rounds down, but
    never to 0 (which blocks every call): 0.5 means "fewer than one a minute", whose nearest limit is 1."""
    if rpm is None:
        return None
    return 0 if rpm <= 0 else max(1, math.floor(rpm))


def model_allowed(model, patterns):
    return any(model == p or (p.endswith("*") and model.startswith(p[:-1])) for p in patterns)
