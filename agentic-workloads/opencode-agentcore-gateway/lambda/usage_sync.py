# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Usage sync (every 5 minutes): per-user model spend from Amazon Bedrock Mantle's CloudWatch metrics.

The interceptor sends every model call with the caller's Mantle project (OpenAI-Project header); Mantle publishes
InputTokens/OutputTokens per Project and Model to CloudWatch (AWS/BedrockMantle). This function sums them per project
for the current month, day and hour, prices them, and SETs model_spend_usd (plus token counts and a per-model
breakdown) on the user's MONTH, DAY and HOUR ledger items, where the interceptor's budget checks read it. Web search
charges are ADDed to spend_usd on the same items by the interceptor, so the two never overwrite each other.

Lag: CloudWatch 1-minute metrics plus this schedule, about 1-6 minutes. A user can therefore overshoot a budget by a
few minutes of traffic; the README states this.

Mantle's InputTokens include cache reads and writes with no split (AWS/BedrockMantle has no cache metric), so all input
is priced at the input rate, or at the model's cache-write rate when PRICES gives a higher `cache_write`: an upper
bound when prompt caching is in use only if cache writes cost no more than that rate.

Per-tag month items (TAG#<dim>=<value> / MONTH#yyyy-mm, model_spend_usd) roll the month up by the user's directory
tags (USER#<u> / PROFILE tags) as they are now.

Env: TABLE, DIRECTORY_FROM_TABLE=1 (users and their Mantle projects from the ledger's CONFIG/DIRECTORY item; without
it, PROJECTS: JSON {user: proj_id}, for tests and local runs), PRICES (JSON {mantle model id: {alias, in, out, cache_write?}} USD per 1M tokens),
     SK_INDEX (optional GSI on sk), TAG_DIMENSIONS (default team,project,environment)
"""
import collections
import concurrent.futures
import contextlib
import datetime as dt
import decimal
import json
import math
import re
import os

import boto3
import botocore.config

import policy

TABLE = os.environ["TABLE"]
PROJECTS = json.loads(os.environ.get("PROJECTS") or "{}")  # with DIRECTORY_FROM_TABLE=1, read per run from the ledger
PRICES = json.loads(os.environ["PRICES"])
SK_INDEX = os.environ.get("SK_INDEX", "")
TAG_DIMS = policy.tag_dims(os.environ)
cw = boto3.client("cloudwatch")
# deferred_writes sends up to 16 writes at once: give the client as many pooled connections (botocore's default is 10).
ddb = boto3.client("dynamodb", config=botocore.config.Config(max_pool_connections=16))


_PRICE_KEYS = {}  # model id -> PRICES key or None; PRICES is fixed for a container, so the answer never changes


def price_key(model):
    """The PRICES key for a Mantle model id: an exact match, else a key the id holds as a whole id, with only a region
    prefix (us.) or a version suffix (:0, @v1, -v1) around it. A different model whose id merely contains a key, such as
    zai.glm-5.3 when only zai.glm-5 is priced, has no price and is reported as unpriced rather than charged at the
    other model's rate."""
    if model in PRICES:
        return model
    # Cached per model: the sync asks for every model of every user in every window, each run.
    if model not in _PRICE_KEYS:
        hits = [k for k in PRICES if re.search(rf"(?:^|[./]){re.escape(k)}(?:$|[:@/]|-v\d)", model)]
        _PRICE_KEYS[model] = max(hits, key=len) if hits else None
    return _PRICE_KEYS[model]


def price(model):
    k = price_key(model)
    return PRICES[k] if k else None


def mantle_metrics():
    """{project: [(key, model, metric)]} from one paginated ListMetrics per metric name, for every project at once
    (one call per user per window ran ~1,000 sequential API calls at 50 users and hit the Lambda timeout)."""
    out = collections.defaultdict(list)
    for metric, key in (("InputTokens", "in"), ("OutputTokens", "out")):
        kw = {"Namespace": "AWS/BedrockMantle", "MetricName": metric}
        for m in policy.paginate_token(cw.list_metrics, "NextToken", ("Metrics",), **kw):
            d = {x["Name"]: x["Value"] for x in m["Dimensions"]}
            # Exactly Project and Model: a metric with further dimensions is a breakdown of the same tokens, and
            # adding it into the (project, model) bucket would count them twice.
            if set(d) == {"Project", "Model"} and d["Project"] and d["Model"]:
                out[d["Project"]].append((key, d["Model"], m))
    return out


def window_tokens(listing, projects, start, end, period):
    """{project: {model: {"in": n, "out": n}}} from start to end (aligned to period), up to 500 queries per call."""
    qs = [(p, key, model, m) for p in projects for key, model, m in listing.get(p, [])]
    out = {p: {} for p in projects}
    for i in range(0, len(qs), 500):
        chunk = qs[i:i + 500]
        kw = {"StartTime": start, "EndTime": end, "MetricDataQueries": [
            {"Id": f"q{j}", "MetricStat": {"Metric": m, "Period": period, "Stat": "Sum"}} for j, (_, _, _, m) in enumerate(chunk)]}
        vals = collections.defaultdict(float)
        for res in policy.paginate_token(cw.get_metric_data, "NextToken", ("MetricDataResults",), **kw):
            vals[res["Id"]] += sum(res.get("Values") or [])
        for j, (p, key, model, _) in enumerate(chunk):
            out[p].setdefault(model, {"in": 0, "out": 0})[key] += vals[f"q{j}"]
    # ListMetrics lists every model used in the last 14 days: drop the ones with no tokens in this window, so no
    # model row or per-model counter is written for a model nobody used in it.
    return {p: {m: v for m, v in ms.items() if v["in"] or v["out"]} for p, ms in out.items()}


def token_cost(model, v):
    """USD of one model's tokens v ({"in", "out"}), or None when the model has no price. The one pricing formula.
    Input is priced at the higher of the input and the optional cache-write rate: Mantle does not say which input
    tokens were cache writes, so this keeps the figure an upper bound for a model whose cache writes cost more (r196)."""
    p = price(model)
    if p is None:
        return None
    rate_in = max(p["in"], p.get("cache_write") or 0)
    return v.get("in", 0) * rate_in / 1e6 + v.get("out", 0) * p["out"] / 1e6


def mantle_cost(t):
    """Cost of Mantle tokens t ({model: {"in": n, "out": n}}): (usd, input tokens, output tokens, unpriced models)."""
    costs = {model: token_cost(model, v) for model, v in t.items()}
    return (sum(c for c in costs.values() if c is not None), sum(v.get("in", 0) for v in t.values()),
            sum(v.get("out", 0) for v in t.values()), [m for m, c in costs.items() if c is None])


def alias_of(model):
    """The ow/<alias> a Mantle model id is listed under (PRICES alias), else the id itself."""
    k = price_key(model)
    return (PRICES[k].get("alias") or k) if k else model


def per_model(t):
    """{alias: {"usd", "in", "out"}} for Mantle tokens t."""
    out = {}
    for model, v in t.items():
        c = out.setdefault(alias_of(model), {"usd": 0.0, "in": 0.0, "out": 0.0})
        c["in"] += v.get("in", 0)
        c["out"] += v.get("out", 0)
        c["usd"] += token_cost(model, v) or 0.0
    return out


_DEFERRED = None
_AFTER = None  # writes that must wait for every _DEFERRED write to succeed (delete_stale's index updates)


@contextlib.contextmanager
def deferred_writes(workers=16):
    """Collect set_rollup writes made inside the block and send them concurrently at its end. The per-user writes
    are independent (one item each, nothing read back), so the run's time no longer grows by one round trip per
    write with the number of users. A failed write raises, as it would inline."""
    global _DEFERRED, _AFTER
    _DEFERRED, _AFTER = [], []
    try:
        yield
        pending, after = _DEFERRED, _AFTER
    finally:
        _DEFERRED = _AFTER = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_send, pending))  # raises on the first failed write, so `after` is not sent
        list(ex.map(_send, after))


def _send(r):
    """One write: {"put": item}, {"delete": key} or UpdateItem keyword arguments."""
    if "put" in r:
        return ddb.put_item(TableName=TABLE, Item=r["put"])
    if "delete" in r:
        return ddb.delete_item(TableName=TABLE, Key=r["delete"])
    return ddb.update_item(**r)


def _write(r, after=False):
    """Queue r on the open deferred_writes() block, or send it now outside one. after=True: send it only once every
    other write of the block has succeeded."""
    if _DEFERRED is not None:
        (_AFTER if after else _DEFERRED).append(r)
    else:
        _send(r)


def token_attrs(t, now):
    """The model spend attributes of a ledger item from Mantle tokens t, in DynamoDB form: totals, model_synced_at and
    m|<alias>|usd/in/out. The one formatting, used by set_rollup (USER# items) and put_rollup (derived rollups)."""
    pm = per_model(t)
    out = {"model_spend_usd": {"N": f"{sum(c['usd'] for c in pm.values()):.8f}"},
           "model_input_tokens": {"N": str(int(sum(c["in"] for c in pm.values())))},
           "model_output_tokens": {"N": str(int(sum(c["out"] for c in pm.values())))},
           "model_synced_at": {"S": now.isoformat()}}
    for a, c in pm.items():
        out[policy.model_attr(a, "usd")], out[policy.model_attr(a, "in")], out[policy.model_attr(a, "out")] = (
            {"N": f"{c['usd']:.8f}"}, {"N": str(int(c["in"]))}, {"N": str(int(c["out"]))})
    return out


SPEND_COUNTERS = ("usd", "in", "out")  # the per-model attributes usage_sync owns (m|<alias>|req is the interceptor's)


def stale_attrs(item, attrs):
    """Per-model usd/in/out attributes on item that attrs no longer carries (a model whose PRICES alias changed or that
    lost its price mid-window), so the next write REMOVEs them instead of leaving them next to the new counters."""
    out = []
    for name in item:
        parsed = policy.parse_model_attr(name)
        if parsed and parsed[1] in SPEND_COUNTERS and name not in attrs:
            out.append(name)
    return sorted(out)


def set_rollup(key, t, now, keep=None, extra_names=None, extra_values=None, extra_set="", attrs=None, remove=()):
    """SET model_spend_usd, model tokens and m|<alias>|usd/in/out on one ledger item from Mantle tokens t (attrs: the
    same, already built by token_attrs), and REMOVE the attributes in remove (stale_attrs)."""
    names, values, sets = dict(extra_names or {}), dict(extra_values or {}), []
    for i, (attr, v) in enumerate(sorted((attrs or token_attrs(t, now)).items())):
        names[f"#a{i}"], values[f":a{i}"] = attr, v
        sets.append(f"#a{i} = :a{i}")
    expr = "SET " + ", ".join(sets) + extra_set
    if keep:
        names["#ttl"] = "ttl"
        values[":ttl"] = {"N": str(int(now.timestamp()) + keep)}
        expr += ", #ttl = if_not_exists(#ttl, :ttl)"
    if remove:
        for i, attr in enumerate(remove):
            names[f"#r{i}"] = attr
        expr += " REMOVE " + ", ".join(f"#r{i}" for i in range(len(remove)))
    kw = {"ExpressionAttributeNames": names} if names else {}
    req = dict(TableName=TABLE, Key=key, UpdateExpression=expr, ExpressionAttributeValues=values, **kw)
    _write(req)  # inside deferred_writes(): sent together, concurrently, when the block ends


def put_rollup(key, t, now, keep=None, requests=0, per_req=None, extra=None):
    """Write one derived rollup row (GROUP#, MODEL#, TAG#) whole, with PutItem. Nothing else writes these rows and each
    run rebuilds them, so a full item leaves no stale counter behind (a model a member stopped using, an unpriced or
    removed model) without tracking what the last run wrote. requests/per_req: the members' request counters;
    extra: further attributes in DynamoDB form (user_set, spend_usd)."""
    item = {**key, **token_attrs(t, now), "requests": {"N": str(int(requests))}, **(extra or {})}
    for a, n in (per_req or {}).items():
        item[policy.model_attr(a, "req")] = {"N": str(int(n))}
    if keep:
        item["ttl"] = {"N": str(int(now.timestamp()) + keep)}
    _write({"put": item})


def has_tokens(t):
    return any(v.get("in") or v.get("out") for v in (t or {}).values())


def should_sync(kind, tokens, stored):
    """The one rule for whether a USER# window item is (re)written this run. Tokens in the window: always. No tokens:
    only a MONTH or DAY item synced before, since it may hold models CloudWatch no longer lists (14-day ListMetrics
    window) or an earlier project's counts (model_base) that its rollups must keep; a never-synced item would just be
    a $0 write for an idle user. An HOUR with no tokens is left alone: its tokens only grow, it has no rollups, and a
    $0 rewrite each run for every user active today would be pure write cost."""
    if has_tokens(tokens):
        return True
    return kind != "HOUR" and bool((stored or {}).get("model_synced_at"))


def merge_tokens(into, t):
    for m, v in t.items():
        c = into.setdefault(m, {"in": 0, "out": 0})
        c["in"] += v.get("in", 0)
        c["out"] += v.get("out", 0)


CLOSE_MINUTES = 20  # re-sync a closed window for this long: past the ~15 min ListMetrics delay for a new metric


def closed_windows(now):
    """Every HOUR/DAY/MONTH window that ended within the last CLOSE_MINUTES, as (kind, sk, start, end, period); the TTL is policy.WINDOW_TTL[kind].
    The one rule for which closed windows get a full-span re-sync: each window ends at a later boundary, so the
    hour closes every hour, the day at 00:00 and the month at 00:00 on the 1st."""
    hour0 = now.replace(minute=0, second=0, microsecond=0)
    if (now - hour0).total_seconds() >= CLOSE_MINUTES * 60:
        return []
    out = [("HOUR", policy.window_sk("HOUR", hour0 - dt.timedelta(hours=1)), hour0 - dt.timedelta(hours=1), hour0, 3600)]
    if hour0.hour == 0:
        out.append(("DAY", policy.window_sk("DAY", hour0 - dt.timedelta(days=1)), hour0 - dt.timedelta(days=1), hour0, 86400))
        if hour0.day == 1:
            pm0 = (hour0 - dt.timedelta(days=1)).replace(day=1)
            out.append(("MONTH", policy.window_sk("MONTH", pm0), pm0, hour0, 86400))
    return out


def _now():
    return dt.datetime.now(dt.timezone.utc)


def batch_get(keys):
    """{(pk, sk): item} for many keys (policy.batch_get: 100 per call, unprocessed keys retried, else the run fails and
    the next one in 5 minutes retries)."""
    return policy.batch_get(ddb, TABLE, keys)


def _json_attr(item, name):
    try:
        v = json.loads(item.get(name, {}).get("S", "{}"))
    except ValueError:
        return {}
    if not isinstance(v, dict):
        return {}
    # Only finite, non-negative counts: a hand-edited value ("lots", -5, a list) is dropped, never a TypeError that
    # stops the whole run for every user.
    return {k: {f: n for f, n in x.items() if _count(n)} for k, x in v.items() if isinstance(x, dict)}


def _count(n):
    return isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) and n >= 0


def sync_window(user, sk, t, now, stored=None, project=None, keep=None):
    """SET window-to-date model spend on a USER# MONTH, DAY or HOUR item from Mantle tokens t of the user's project
    (stored: the item, if already read; keep: the TTL for a DAY or HOUR item).

    The item keeps the current project's counts (model_by_model, with model_project) apart from the final counts of any
    earlier project in the window (model_base). A user removed and re-added gets a new Mantle project whose CloudWatch
    window starts at 0: its usage is added on top of the old project's, not hidden under it by max(), so the daily and
    hourly caps keep counting what the old project spent."""
    key = {"pk": {"S": f"USER#{user}"}, "sk": {"S": sk}}
    m = stored if stored is not None else ddb.get_item(TableName=TABLE, Key=key).get("Item", {})
    base = _json_attr(m, "model_base")
    # ListMetrics only returns metrics with data in the last 14 days, so a model last used earlier in a month would
    # drop out and the SET below would shrink the window. Keep the window-to-date tokens stored by the previous sync
    # for any model CloudWatch no longer lists (they cannot have grown without new data points).
    stored = _json_attr(m, "model_by_model")
    was = m.get("model_project", {}).get("S")
    if project and was and was != project:  # the project changed: the old one's window is final, move it to the base
        merge_tokens(base, stored)
        stored = {}
    # Merge per metric, not per model: InputTokens and OutputTokens are listed separately (each with its own delay), so a
    # model listed for one of them reads 0 for the other. Window-to-date counts only grow, so keep the larger of each.
    merged = stored
    for model, v in t.items():
        cur = merged.setdefault(model, {})
        for f, n in v.items():
            cur[f] = max(cur.get(f, 0) or 0, n or 0)
    total = {k: dict(v) for k, v in base.items()}
    merge_tokens(total, merged)
    cost, tin, tout, unpriced = mantle_cost(total)
    extra_values, extra_set = {":m": {"S": json.dumps(merged)}, ":b": {"S": json.dumps(base)}}, ", model_by_model = :m, model_base = :b"
    if project:
        extra_values[":p"] = {"S": project}
        extra_set += ", model_project = :p"
    attrs = token_attrs(total, now)  # priced and formatted once, for the comparison and the write
    also = {"model_by_model": extra_values[":m"], "model_base": extra_values[":b"], **({"model_project": extra_values[":p"]} if project else {})}
    stale = stale_attrs(m, attrs)
    if stale or not unchanged(m, attrs, also):
        set_rollup(key, total, now, keep, extra_values=extra_values, extra_set=extra_set, attrs=attrs, remove=stale)
    return cost, tin, tout, unpriced, total, m


def unchanged(item, attrs, also):
    """True when a synced item already holds exactly what this run would SET (all but the model_synced_at time), so
    an idle user's MONTH and DAY items are not rewritten every 5 minutes. Prices are part of the values compared, so a
    price change still rewrites."""
    if not item.get("model_synced_at"):
        return False
    want = {**{k: v for k, v in attrs.items() if k != "model_synced_at"}, **also}

    def same(have, v):  # DynamoDB stores numbers normalised ("0.10000000" reads back as "0.1"): compare N by value
        if have is None or set(have) != set(v):
            return False
        if "N" in v:
            return decimal.Decimal(have["N"]) == decimal.Decimal(v["N"])
        return have == v
    return all(same(item.get(k), v) for k, v in want.items())


def requests_of(item):
    """(total, {model alias: n}, web search usd) from the interceptor's counters on a USER# window item. Web search
    spend (spend_usd) is ADDed by the interceptor, not derived from Mantle tokens, so rollups carry it from here."""
    item = item or {}
    per = {mc[0]: int(policy.num_attr(item, k)) for k in item if (mc := policy.parse_model_attr(k)) and mc[1] == "req"}
    return int(policy.num_attr(item, "requests")), per, policy.amount(item, "spend_usd")[0]  # a bad edit adds 0


INDEX_PKS = ("ROLLUPINDEX", "MODELINDEX", "TAGINDEX")
_INDEX = {}  # (index pk, sk) -> index item or None, read ahead by prefetch_indexes for this run


def prefetch_indexes(sks):
    """Read every rollup index item this run will consult in one parallel batch, instead of one GetItem per index and
    window in a row (6 in the steady state, up to 10 after a rollover). Missing items are recorded as None."""
    _INDEX.clear()
    # TAG# rows are monthly only, so TAGINDEX is read for MONTH windows only.
    keys = [(ipk, sk) for sk in sks for ipk in INDEX_PKS if ipk != "TAGINDEX" or sk.startswith("MONTH#")]
    got = batch_get(keys)
    _INDEX.update({k: got.get(k) for k in keys})


def delete_stale(index_pk, sk, prefix, current, now, keep):
    """Delete the <prefix>* rollup rows for sk written last run but not this one, then record this run's set.

    The set written last run is kept on one index item (<index_pk>, sk), so this is a GetItem rather than a scan of the
    ledger every 5 minutes; without the index (the first run for sk) it reads the rows once through the sk index."""
    idx_key = {"pk": {"S": index_pk}, "sk": {"S": sk}}
    idx = _INDEX.pop((index_pk, sk)) if (index_pk, sk) in _INDEX else ddb.get_item(TableName=TABLE, Key=idx_key).get("Item")
    known = ({x["S"] for x in idx["keys"]["L"]} if idx and "keys" in idx
             else {it["pk"]["S"] for it in policy.by_sk(ddb, TABLE, SK_INDEX, sk, prefix)})
    # The deletes touch other items than this run's puts, so inside deferred_writes() they go out with them,
    # concurrently. The index update goes after all of them: if a delete fails, the index still lists the stale row,
    # so the next run retries the delete instead of forgetting it.
    def index(keys):
        return {"TableName": TABLE, "Key": idx_key, "UpdateExpression": "SET #k = :k, updated_at = :t, #ttl = :ttl",
                "ExpressionAttributeNames": {"#k": "keys", "#ttl": "ttl"},
                "ExpressionAttributeValues": {":k": {"L": [{"S": k} for k in sorted(keys)]}, ":t": {"S": now.isoformat()},
                                              ":ttl": {"N": str(int(now.timestamp()) + keep)}}}
    # Rows this run creates are listed first, inline, before any put goes out: if some write then fails, the index
    # still names every row that may exist, so a later run can delete the new one once it is stale too.
    if current - known:
        _send(index(known | current))
    for pk in sorted(known - current):
        if pk.startswith(prefix):
            _write({"delete": {"pk": {"S": pk}, "sk": {"S": sk}}})
    _write(after=True, r=index(current))


def write_group_model_rollups(sk, per_user, user_groups, now, per_user_req=None):
    """GROUP#<g> and MODEL#<alias> items for one window (sk) from each user's Mantle tokens in that window.

    Groups are the users' groups as they are now. A group that no longer has any member with usage in the window is
    deleted, so a user who moves group is never counted under both (the GROUP# pks written last time are kept on a
    ROLLUPINDEX item for the window, so this is a GetItem rather than a scan)."""
    per_user_req = per_user_req or {}
    # Rollup rows live as long as the USER# rows of the same window (policy.WINDOW_TTL), the month ones included.
    keep = policy.WINDOW_TTL[sk.split("#", 1)[0]]
    grp, mdl, mreq = {}, {}, collections.Counter()
    for user, t in per_user.items():
        total, per, ws = per_user_req.get(user, (0, {}, 0.0))
        if not total and not has_tokens(t) and not ws:
            continue  # no usage in this window: not a member "seen" here, and no $0 row for an idle group
        for g in user_groups.get(user) or [policy.NO_GROUP]:
            v = grp.setdefault(g, {"users": set(), "t": {}, "req": 0, "mreq": collections.Counter(), "ws": 0.0})
            v["users"].add(user)
            merge_tokens(v["t"], t)
            v["req"] += total
            v["ws"] += ws
            v["mreq"].update(per)
        merge_tokens(mdl, t)
        mreq.update(per)
    for g, v in grp.items():
        put_rollup({"pk": {"S": f"GROUP#{g}"}, "sk": {"S": sk}}, v["t"], now, keep, v["req"], v["mreq"],
                   {"user_set": {"SS": sorted(v["users"])}, "spend_usd": {"N": f"{v['ws']:.8f}"}})
    by_alias = {}  # one pass: each served model under the alias it is billed to
    for m, x in mdl.items():
        by_alias.setdefault(alias_of(m), {})[m] = x
    models = set(by_alias) | set(mreq)
    for a in models:
        put_rollup({"pk": {"S": f"MODEL#{a}"}, "sk": {"S": sk}}, by_alias.get(a, {}), now, keep, mreq.get(a, 0))
    # A group with no member, or a model nobody used, in this window must not keep its old row. Each index item lives
    # as long as the rows it lists.
    delete_stale("ROLLUPINDEX", sk, "GROUP#", {f"GROUP#{g}" for g in grp}, now, keep)
    delete_stale("MODELINDEX", sk, "MODEL#", {f"MODEL#{a}" for a in models}, now, keep)


class TagRollup:
    """One month's TAG#<dimension>=<value> rollups: the members' tokens, web search spend and request counters, summed
    per (dimension, value), then written whole and the stale values removed. The same rules serve the open month and
    the month that just closed."""

    def __init__(self):
        self.tok = collections.defaultdict(dict)
        self.ws = collections.Counter()  # web search usd per (dimension, value)
        self.req = collections.defaultdict(lambda: [0, collections.Counter()])

    def add(self, tv, t, r):
        """Add one user's month tokens t and requests_of() r. Idle users add no key, so no $0 row is written for a value
        nobody used."""
        if not (has_tokens(t) or r[0] or r[2]):
            return
        merge_tokens(self.tok[tv], t)
        self.ws[tv] += r[2]
        self.req[tv][0] += r[0]
        self.req[tv][1].update(r[1])

    def write(self, sk, now):
        # Tokens give the model spend; spend_usd carries the members' web search spend; the request and per-model
        # counters let a usage query by tag show requests and filter by model.
        keep = policy.WINDOW_TTL["MONTH"]  # as long as the USER# month rows, and the index listing them as long
        for (d, v), t in self.tok.items():
            put_rollup({"pk": {"S": f"TAG#{d}={v}"}, "sk": {"S": sk}}, t, now, keep, *self.req[(d, v)],
                       {"spend_usd": {"N": f"{self.ws[(d, v)]:.8f}"}})
        # A tag value whose last user moved to another value still holds its old figure; delete it, or the spend is
        # counted under both the old and the new value.
        delete_stale("TAGINDEX", sk, "TAG#", {f"TAG#{d}={v}" for d, v in self.tok}, now, keep)


def lambda_handler(event=None, context=None):
    global PROJECTS  # noqa: PLW0603 - refreshed from the ledger's CONFIG/DIRECTORY item (policy.directory)
    if os.environ.get("DIRECTORY_FROM_TABLE") == "1":
        PROJECTS = policy.directory(ddb, TABLE, os.environ)[0]
    elif "PROJECTS" not in os.environ:
        # Neither source configured: syncing nobody would leave every budget at $0 of model spend (fail open). Stop.
        raise RuntimeError("usage_sync needs DIRECTORY_FROM_TABLE=1 (Terraform sets it) or PROJECTS")
    now = _now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = now + dt.timedelta(minutes=1)
    result, tags_now, tags_prev = {}, TagRollup(), TagRollup()  # the open month's tags, and the month that just closed
    # request counts per (dimension, value): [total, Counter of model alias -> n], as GROUP# rows carry them
    day0, hour0 = now.replace(hour=0, minute=0, second=0, microsecond=0), now.replace(minute=0, second=0, microsecond=0)
    listing, projects = mantle_metrics(), sorted(set(PROJECTS.values()))
    # The three open windows are independent GetMetricData passes: run them at the same time. The day could be read
    # from the month pass's last daily bucket instead, but the daily cap would then rest on how CloudWatch aligns and
    # timestamps those buckets, which the tests cannot check without a live account; a separate day pass costs about a
    # third more GetMetricData calls (a few cents a month for a team) and gives the cap its own exact window.
    # CloudWatch lags 1-6 minutes, and ListMetrics can take up to about 15 minutes to list a project's first metric for
    # a model, so the last run of a window misses its final minutes: for CLOSE_MINUTES (20) after an hour,
    # day or month rolls over, also re-sync the window that just closed over its full span. Those re-syncs are
    # independent passes too, so every pass (open and closed) shares one pool and runs at the same time.
    closing = closed_windows(now)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        # Each open window ends at min(now + 1 min, the window's own end): past it, CloudWatch returns the next
        # period's bucket too, and its tokens would be summed into the window that is closing.
        month_end = (start + dt.timedelta(days=32)).replace(day=1)
        futs = {w: ex.submit(window_tokens, listing, projects, lo, min(end, hi), period)
                for w, lo, hi, period in (("day", day0, day0 + dt.timedelta(days=1), 86400),
                                          ("hour", hour0, hour0 + dt.timedelta(hours=1), 3600),
                                          ("month", start, month_end, 86400))}
        tok = {sk: ex.submit(window_tokens, listing, projects, lo, hi, period) for _, sk, lo, hi, period in closing}
        by_window = {w: f.result() for w, f in futs.items()}
        tok = {sk: f.result() for sk, f in tok.items()}
    closed = {sk: (tok[sk], policy.WINDOW_TTL[kind]) for kind, sk, *_ in closing if kind != "MONTH"}
    prev_month = next(((sk, tok[sk]) for kind, sk, *_ in closing if kind == "MONTH"), None)
    user_groups, month_t, day_t, closed_t, prev_t = {}, {}, {}, collections.defaultdict(dict), {}
    month_r, day_r, closed_r, prev_r = {}, {}, collections.defaultdict(dict), {}
    month_sk, day_sk, hour_sk = (policy.window_sk(k, now) for k in ("MONTH", "DAY", "HOUR"))
    # Every window item is read first: sync_window carries an earlier project's counts (model_base) on each of them.
    pre = batch_get([(f"USER#{u}", sk) for u in PROJECTS
                     for sk in ("BUDGET", "PROFILE", month_sk, day_sk, hour_sk, *closed,
                                *([prev_month[0]] if prev_month else []))])
    with deferred_writes():  # each user's item writes go out together, concurrently, after the loop
        for user, project in PROJECTS.items():
            got = lambda sk: pre.get((f"USER#{user}", sk))  # noqa: E731, B023 - used inside this iteration only
            for sk, (wtok, keep) in closed.items():
                # The full-span re-sync covers every earlier partial sync of this window. No tokens now means nothing new
                # to SET: skip the write rather than SET a $0 item for each idle user. A closed DAY already synced is
                # re-synced anyway, because it may carry an earlier project's counts (model_base) that the closed day's
                # GROUP#/MODEL# rows must still include; hours have no rollups, so an idle hour is left as it is.
                stored = got(sk) or {}
                is_day = sk.startswith("DAY#")
                if should_sync(sk.split("#", 1)[0], wtok[project], stored):
                    tot = sync_window(user, sk, wtok[project], now, stored=stored, project=project, keep=keep)[4]
                else:
                    tot = wtok[project]
                if is_day:  # closed_t/closed_r hold DAY# keys only: they feed the closed day's rollups
                    closed_t[sk][user] = tot
                    closed_r[sk][user] = requests_of(stored)
            if prev_month:
                pm_stored = got(prev_month[0]) or {}
                # Same rule as the open month: a month synced before keeps its stored per-model tokens (ListMetrics drops
                # models idle for 14 days), so only a never-synced month with no tokens is skipped.
                if should_sync("MONTH", prev_month[1][project], pm_stored):
                    pm = sync_window(user, prev_month[0], prev_month[1][project], now, stored=pm_stored, project=project, keep=policy.WINDOW_TTL["MONTH"])
                    prev_t[user], prev_r[user] = pm[4], requests_of(pm[5])
                else:
                    prev_t[user], prev_r[user] = {}, requests_of(pm_stored)
            # Daily and hourly caps read the DAY/HOUR items, so model spend is SET there too (rule: should_sync).
            day_idle = not should_sync("DAY", by_window["day"][project], got(day_sk))
            hour_idle = not should_sync("HOUR", by_window["hour"][project], got(hour_sk))
            day_tok = by_window["day"][project]
            for sk, win, keep, skip in ((day_sk, "day", policy.WINDOW_TTL["DAY"], day_idle), (hour_sk, "hour", policy.WINDOW_TTL["HOUR"], hour_idle)):
                if not skip:
                    tot = sync_window(user, sk, by_window[win][project], now, stored=got(sk) or {}, project=project, keep=keep)[4]
                    if win == "day":
                        day_tok = tot
            stored = got(month_sk) or {}
            if not should_sync("MONTH", by_window["month"][project], stored):
                cost, tin, tout, unpriced, t, m = 0.0, 0, 0, [], {}, stored
            else:
                cost, tin, tout, unpriced, t, m = sync_window(user, month_sk, by_window["month"][project], now, stored=stored, project=project,
                                                              keep=policy.WINDOW_TTL["MONTH"])  # every window item gets its ttl
            result[user] = {"project": project, "model_spend_usd": round(cost, 6), "websearch_spend_usd": round(policy.amount(m, "spend_usd")[0], 6), "input_tokens": int(tin), "output_tokens": int(tout), "by_model": t, "unpriced": unpriced}
            budget = got("BUDGET") or {}
            # The dashboard's rule, shared through policy.budget_groups; a user with no group is filed under "(no group)".
            user_groups[user] = policy.budget_groups(budget)  # groupless users are filed under NO_GROUP by the rollups
            month_t[user], day_t[user] = t, day_tok
            month_r[user] = requests_of(m)
            day_r[user] = requests_of(got(day_sk))
            prof = got("PROFILE") or {}
            # String tag values only: a hand-edited tag of another type is skipped, not a KeyError for the whole run.
            raw_tags = prof.get("tags", {}).get("M", {}) if isinstance(prof.get("tags"), dict) else {}
            tags = {k: v["S"] for k, v in raw_tags.items() if isinstance(v, dict) and isinstance(v.get("S"), str)}
            for d in TAG_DIMS:
                tv = (d, tags.get(d) or "unassigned")
                tags_now.add(tv, t, month_r[user])
                if prev_month:
                    tags_prev.add(tv, prev_t[user], prev_r[user])
    # GROUP#<g> and MODEL#<alias> rollups for the month and today (the dashboard's group and model views read them).
    # The day and month that just closed are rolled up again too, so their last minutes of usage reach the group and
    # model views as they reach the user rows.
    # The GROUP#, MODEL# and TAG# writes (and delete_stale's deletes) are independent too; delete_stale's index items
    # are read ahead in one batch.
    prefetch_indexes([month_sk, day_sk, *closed_t, *([prev_month[0]] if prev_month else [])])
    try:
        _write_rollups(month_sk, day_sk, month_t, day_t, month_r, day_r, closed_t, closed_r, prev_month, prev_t, prev_r,
                       user_groups, tags_now, tags_prev, now)
    finally:
        _INDEX.clear()  # a run that stopped part way must not leave read-ahead index items for a later call
    return _finish(result, tags_now, closing)


def _write_rollups(month_sk, day_sk, month_t, day_t, month_r, day_r, closed_t, closed_r, prev_month, prev_t, prev_r,
                   user_groups, tags_now, tags_prev, now):
    with deferred_writes():
        write_group_model_rollups(month_sk, month_t, user_groups, now, per_user_req=month_r)
        write_group_model_rollups(day_sk, day_t, user_groups, now, per_user_req=day_r)
        for sk, per_user in closed_t.items():  # closed days only (closed_t holds DAY# keys)
            write_group_model_rollups(sk, per_user, user_groups, now, per_user_req=closed_r[sk])
        if prev_month:  # the month that just closed: its final group/model and tag figures (tags as they are now)
            write_group_model_rollups(prev_month[0], prev_t, user_groups, now, per_user_req=prev_r)
            tags_prev.write(prev_month[0], now)
        tags_now.write(month_sk, now)


def _finish(result, tags_now, closing):
    result["_tags"] = {f"{d}={v}": round(mantle_cost(t)[0] + tags_now.ws[(d, v)], 6) for (d, v), t in tags_now.tok.items()}
    # One short summary line per run (the full per-user result grows with users x models and is logged 288 times a day).
    users = [v for k, v in result.items() if not k.startswith("_")]
    print(json.dumps({"outcome": "synced", "users": len(users), "model_spend_usd": round(sum(v["model_spend_usd"] for v in users), 6),
                      "websearch_spend_usd": round(sum(v["websearch_spend_usd"] for v in users), 6),
                      "unpriced": sorted({m for v in users for m in v["unpriced"]}), "closed_windows": [sk for _, sk, *_ in closing]}))
    return result
