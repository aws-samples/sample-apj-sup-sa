# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Dashboard API v2 (contract: dashboard/ui-src/API.md). Called by dashboard.py after the ALB sign-in check.

Reads the usage rollups and policy items of the ledger table; writes policy items, user overrides and boosts, and the
repair of an unreadable usage record (each with an AUDIT item in the same transaction); reads the gateway's rate
limits (Terraform sets them). The interceptor picks policy changes up within policy.CACHE_SECONDS.
"""
import calendar
import collections
import concurrent.futures
import hashlib
import itertools
import json
import os
import re
import secrets
import time
import urllib.parse
import uuid

import boto3
import botocore.config

import policy

TABLE = os.environ["TABLE"]
GROUP_MODELS = json.loads(os.environ.get("GROUP_MODELS", "{}"))
MODEL_CATALOG = json.loads(os.environ.get("MODEL_CATALOG", "[]"))
MODEL_ALIASES = json.loads(os.environ.get("MODEL_ALIASES", "{}"))
PRICES = json.loads(os.environ.get("PRICES", "{}"))  # model id -> {input, output} USD per 1M tokens
IDENTITY_MAP = json.loads(os.environ.get("IDENTITY_MAP", "{}"))  # IdP user id -> user name, without the directory item
GATEWAY_ID = os.environ.get("GATEWAY_ID", "")
ENV_LIMITS = policy.env_limits(os.environ)  # the same parsing as the interceptor
ddb = boto3.client("dynamodb", config=botocore.config.Config(max_pool_connections=16))  # one connection per _POOL thread
_acc = None  # bedrock-agentcore-control client, created on first use

# dashboard field -> ledger attributes summed into it. model_* are SET by usage_sync from Mantle metrics; spend_usd holds
# web search charges (ADDed by the interceptor); requests is ADDed by the interceptor for each allowed model call.
# Mantle reports no cache split (its InputTokens include cache reads and writes), so there are no cache counters: all
# input is priced at the input rate (or a higher cache_write rate, usage_sync.token_cost), an upper bound for cached
# prompts only where cache writes cost no more than that.
COUNTERS = {"usd": policy.SPEND_ATTRS, "input": ("model_input_tokens",), "output": ("model_output_tokens",),
            "requests": ("requests",)}
MCOUNT = {"usd": "usd", "input": "in", "output": "out", "requests": "req"}
NAME = re.compile(r"^[A-Za-z0-9._@+-]{1,128}$")
# IdP group names may hold spaces and other punctuation (the interceptor splits group claims on commas only); refuse only
# what cannot be a group name or would break a path or a log line.
GROUP_NAME = re.compile(r"^[^\x00-\x1f\x7f/\\,]{1,256}$")
USER_ATTR = {"monthly_usd": "budget_usd", "daily_usd": "daily_usd", "hourly_usd": "hourly_usd", "rpm": "rpm"}


# C0, DEL and C1 controls (U+009B is CSI in terminals that honour 8-bit controls), zero-width characters, direction
# marks and the BOM, which can hide or reorder text (r197).
_CONTROL_OR_BIDI = re.compile("[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]")


class ApiError(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.body = status, {"error": message, **extra}


# ---------------- table access ----------------

def _paginate(op, **kw):
    return list(policy.paginate(ddb, op, TableName=TABLE, **kw))


SK_INDEX = os.environ.get("SK_INDEX", "")  # GSI keyed on sk (ledger.tf by_sk); unset = filtered scans


def scan_sk(sk, kind=None):
    """Every item with this sort key (and a pk starting with kind): one Query on the sk index, else a filtered Scan."""
    return list(policy.by_sk(ddb, TABLE, SK_INDEX, sk, kind))


def budget_items():
    """Every user's BUDGET item (pk USER#<user>): the one reader, so no other item with sk BUDGET passes for a user."""
    return scan_sk("BUDGET", "USER#")


def _by_sk_many(sks, kind=None):
    """One index Query per sort key, run in parallel (a month of DAY# rows is up to 31 queries)."""
    return [it for rows in _POOL.map(lambda sk: scan_sk(sk, kind), sks) for it in rows]


def _days_of(month):
    y, m = map(int, month.split("-"))
    return [f"{month}-{d:02d}" for d in range(1, calendar.monthrange(y, m)[1] + 1)]


def scan_range(kind, lo, hi, sks=None):
    """Items whose pk starts with kind and whose sk is in [lo, hi] (with the index: one Query per sk in sks)."""
    if SK_INDEX and sks is not None:
        return _by_sk_many(sks, kind)
    return _paginate("scan", FilterExpression="begins_with(pk, :k) AND sk BETWEEN :lo AND :hi",
                     ExpressionAttributeValues={":k": {"S": kind}, ":lo": {"S": lo}, ":hi": {"S": hi}})


def query(pk, lo=None, hi=None):
    """Every item of pk (with sk in [lo, hi] when given), oldest first, over every page."""
    kw = {"KeyConditionExpression": "pk = :p", "ExpressionAttributeValues": {":p": {"S": pk}}}
    if lo is not None:
        kw["KeyConditionExpression"] += " AND sk BETWEEN :lo AND :hi"
        kw["ExpressionAttributeValues"].update({":lo": {"S": lo}, ":hi": {"S": hi}})
    return _paginate("query", **kw)


def get(pk, sk, consistent=False):
    return ddb.get_item(TableName=TABLE, Key={"pk": {"S": pk}, "sk": {"S": sk}}, ConsistentRead=consistent).get("Item")


def counters(it, raw=False):
    """A row's counters, rounded for display (raw: unrounded floats, for arithmetic). usd is policy.window_spend, the
    same sum the interceptor caps on."""
    c = {k: (policy.window_spend(it) if k == "usd" else sum(policy.num_attr(it, a) for a in attrs)) for k, attrs in COUNTERS.items()}
    return c if raw else {k: (round(v, 6) if k == "usd" else int(v)) for k, v in c.items()}


# Spend with no per-model counter: web search charges (spend_usd, ADDed by the interceptor before the search runs), any
# model spend usage_sync could not price, and the month's spend of a user removed from var.users (usage_sync rebuilds
# GROUP#/MODEL#/TAG# rows from listed users only; the user's own MONTH row keeps it). Each view derives it from what it already reads (the overview's daily series
# from USER# DAY spend_usd, the model mix and the usage explorer as the remainder of the row's total), so no extra
# counter has to be kept in step with spend_usd.
UNATTRIBUTED = "(unattributed)"
# Smallest gap shown as (unattributed): rows are stored and summed with rounding (8 and 6 decimals), so with many users
# the sums of USER# and MODEL# rows drift apart by a few millionths even when every dollar has a model behind it.
GAP_MIN = 0.001
AUDIT_PAGE = 500  # newest audit rows per /audit request
REASON_MAX = 1000  # characters in a write's audit reason
class _RequestPool(concurrent.futures.ThreadPoolExecutor):
    """The thread pool, remembering what each request submitted: handle() waits for all of it before it returns, on
    success and on error alike, so a view that fails part-way never leaves a read running into the next invocation
    (Lambda freezes the container with it). One request runs at a time per container, so one list serves."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._mine = []

    def submit(self, fn, /, *a, **kw):
        f = super().submit(fn, *a, **kw)
        self._mine.append(f)
        return f

    def map(self, fn, *its, timeout=None, chunksize=1):
        """Executor.map's contract (results in order, timeout one deadline for all, the calls not started cancelled
        when the results are abandoned or one raises), built on submit so every call is recorded for drain(). Kept
        explicit on purpose: that the stdlib map goes through self.submit is a CPython detail, not documented API,
        and drain()'s guarantee must not rest on it."""
        end = None if timeout is None else time.monotonic() + timeout
        fs = [self.submit(fn, *args) for args in zip(*its)]

        def results():
            try:
                for f in fs:
                    yield f.result(None if end is None else max(end - time.monotonic(), 0))
            finally:
                for f in fs:
                    f.cancel()
        return results()

    def drain(self):
        mine, self._mine = self._mine, []
        concurrent.futures.wait(mine)


# One pool for the parallel reads (index Queries, GetItems, gateway reads), kept across invocations of a warm container.
# Tasks run on it never submit to it, so it cannot deadlock on itself.
_POOL = _RequestPool(max_workers=16)
# from/to on the audit log: a date or a whole-second UTC time, the same shape as the sk "<YYYY-MM-DDTHH:MM:SSZ>#<id>"
# they are compared with as text (a shorter time such as T10Z would sort after T10:15:00Z and drop rows).
AUDIT_TIME = re.compile(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z)?")
NO_GROUP = policy.NO_GROUP  # usage_sync files users whose token has no groups here
TAG_DIMS = policy.tag_dims(os.environ)  # [] when tags are off


def by_model(it, raw=False):
    """Per-model counters from the flat m|<model>|<c> attributes (raw: unrounded floats)."""
    out = {}
    for k in it or {}:
        if (mc := policy.parse_model_attr(k)):
            model, c = mc
            key = next((a for a, b in MCOUNT.items() if b == c), None)
            if key:
                out.setdefault(model, {a: 0 for a in MCOUNT})[key] += policy.num_attr(it, k)
    return out if raw else {m: {k: (round(x, 6) if k == "usd" else int(x)) for k, x in c.items()} for m, c in out.items()}


def model_parts(it):
    """A row's per-model counters plus its (unattributed) remainder, the spend with no per-model counter (web search,
    unpriced, see UNATTRIBUTED), so the parts add up to the row. One pass over the raw figures, rounded at the end;
    the stored attributes differ only by their 8-decimal storage, so a remainder above a millionth is real spend."""
    raw = by_model(it, raw=True)
    rest = counters(it, raw=True)
    for c in raw.values():
        rest = {k: rest[k] - c.get(k, 0) for k in rest}
    parts = {m: {k: (round(x, 6) if k == "usd" else int(x)) for k, x in c.items()} for m, c in raw.items()}
    if rest.get("usd", 0) > 1e-6:
        parts[UNATTRIBUTED] = {k: (round(v, 6) if k == "usd" else max(0, int(v))) for k, v in rest.items()}
    return parts


def zero():
    return {k: 0 if k != "usd" else 0.0 for k in COUNTERS}


def add(a, b):
    return {k: (round(a[k] + b[k], 6) if k == "usd" else a[k] + b[k]) for k in a}


def now_parts(now=None):
    t = now or time.time()  # one clock read: callers take every time of a response from here ("t"), never re-read it
    g = time.gmtime(t)
    # The parts after "#" of policy.window_sk, the keys usage_sync writes ("DAY#" + p["day"] is that day's sort key).
    month, day, hour = (policy.window_sk(k, g).split("#", 1)[1] for k in ("MONTH", "DAY", "HOUR"))
    return {"month": month, "day": day, "hour": hour, "g": g, "t": t}


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def resets_at(period, g):
    t = calendar.timegm(g)
    if period == "Hourly":
        return iso(t - t % 3600 + 3600)
    if period == "Daily":
        return iso(t - t % 86400 + 86400)
    y, m = (g.tm_year + (g.tm_mon == 12), g.tm_mon % 12 + 1)
    return iso(calendar.timegm((y, m, 1, 0, 0, 0)))


# ---------------- users, groups, limits ----------------

def user_groups(budget_rows=None, only=None, budget_item=None):
    """user -> groups, by one rule for the list and for one user: the groups the interceptor last saw on the user's token
    (BUDGET token_groups, an empty list included, which is what it applies), else the group Terraform seeded on the
    BUDGET item (a user who has not called yet). A user with no BUDGET item (removed from var.users, so unable to call)
    has no groups, in the list and on the user page alike. `only` reads one BUDGET item, or uses
    `budget_item` when the caller already holds it."""
    if only:
        return {only: policy.budget_groups(budget_item if budget_item is not None else get(f"USER#{only}", "BUDGET"))}
    budgets = {it["pk"]["S"][5:]: it for it in (budget_rows if budget_rows is not None else budget_items())
               if it["pk"]["S"].startswith("USER#")}
    return {u: policy.budget_groups(it) for u, it in budgets.items()}


def boost_state(p, now=None):
    """What the BUDGET item holds of a boost (p: its plain values, cleaned here): "live" (in force), "expired" (a
    whole boost whose end has passed: nothing removes the attributes), "unreadable" (a hand edit, which limits() reads as no amount),
    or None (no boost attributes). End boost clears any of them."""
    now = now or time.time()
    b = policy.limits(p or {})
    if policy.boost_live(b, now):
        return "live"
    if not any(a in (p or {}) for a in BOOST_ATTRS):
        return None
    # limits() reads a hand edit (policy._boost_hand_edit) as no amount; a whole boost keeps its amount after its end.
    return "expired" if b.get("boost_usd") else "unreadable"


def boost_view(b, eff, now=None):
    """A live boost as the dashboard shows it (b: plain BUDGET item, eff: policy.resolve of it), or None. in_effect is
    False when the boost's period has no cap (removed after the grant) or a $0 cap from the user, a group or the org
    (a block): the boost is held back, and applies again if a cap above $0 is set before it ends."""
    if not policy.boost_live(b, now):
        return None
    period = b.get("boost_period", "Monthly")
    return {"usd": b["boost_usd"], "period": period, "until": iso(b["boost_until"]), "reason": b.get("boost_reason"),
            "in_effect": eff["caps"].get(period, {}).get("boost", 0) > 0}


def limits_for(b, groups, items, warn, g, pols=None, now=None):
    """The user's limit rows from their raw window items ({"Monthly": item, ...}), by the gate's own rules: spend and
    readability from policy.window_read. An unreadable record is flagged on every window; a capped one is also
    blocked (the gate refuses on it), an uncapped one blocks nothing but would the moment a cap is set. b: the BUDGET
    item as policy.read_item reads it ({} for none); now: the page's one clock read (boosts in force)."""
    seen = {w: policy.window_read(it) for w, it in items.items()}
    spent = {w: v for w, (v, _) in seen.items()}
    eff = policy.resolve(b, pols if pols is not None else policy.policy_items(ddb, TABLE), groups, ENV_LIMITS, GROUP_MODELS, now)
    rows = []
    for period in ("Monthly", "Daily", "Hourly"):
        c = eff["caps"][period]
        raw = spent[period] / c["cap"] * 100 if c["cap"] else (100.0 if c["cap"] == 0 else None)
        # "blocked" is the interceptor's own rule (policy.at_cap) and the warn levels use the unrounded share, so a
        # rounded 100.0% the gateway still allows shows as critical, not blocked.
        bad = not seen[period][1]  # flagged on every window; refused at the gate where capped (policy.refused)
        # An unreadable record has no amount to show: its spend and share are null, never a partial sum that looks real.
        state = ("blocked" if policy.refused(seen[period][1], spent[period], c["cap"]) else "warn" if bad else
                 "none" if raw is None else "critical" if raw >= warn[1] else "warn" if raw >= warn[0] else "ok")
        pct = None if raw is None or bad else round(raw, 1)
        rows.append({"period": period, "cap": c["cap"], "base": c["base"], "source": c["source"], "boost": c["boost"],
                     "spent": None if bad else round(spent[period], 6),
                     "pct": pct, "state": state, "unreadable": bad, "resets_at": resets_at(period, g)})
    return rows, eff


WORST = ["none", "ok", "warn", "critical", "blocked"]


def listed_user(pk):
    """The user name of a USER#<name> ledger pk the dashboard shows, else None. Legacy rows keyed by a raw claims dump
    ("{...}") or "unknown" are left out of every view alike (Users, Overview totals, Usage explorer), so the totals
    are the sum of the rows a reader can see."""
    if not pk.startswith("USER#"):
        return None
    name = pk[5:]
    return None if name.startswith("{") or name == "unknown" else name


def users_view(rows=None, then=None, p=None):
    """Per-user usage and limits. `rows`, when a dict, receives this month's and today's scans for the caller to reuse.
    `then`, if given, is called once this view's reads are queued (its result goes in rows["then"]), so a caller can
    queue its own independent reads behind them instead of in front. `p`: the caller's now_parts(), so both read the
    same month, day and hour even across midnight."""
    p = p or now_parts()
    # Four independent reads (one Query each with the sk index), queued first so they are not behind the caller's;
    # month and BUDGET are shared with user_groups below.
    futs = [_POOL.submit(scan_sk, sk, "USER#" if sk == "BUDGET" else None) for sk in (f"MONTH#{p['month']}", "BUDGET", f"DAY#{p['day']}", f"HOUR#{p['hour']}")]
    if then is not None:
        rows["then"] = then()
    pols = policy.policy_items(ddb, TABLE)
    warn = policy.warn_pct(pols.get("ORG"))
    month_all, budget_all, day_all, hour_all = (f.result() for f in futs)
    month, day, hour = ({it["pk"]["S"][5:]: it for it in items if it["pk"]["S"].startswith("USER#")}
                        for items in (month_all, day_all, hour_all))
    budgets = {it["pk"]["S"][5:]: it for it in budget_all if it["pk"]["S"].startswith("USER#")}
    ug = user_groups(budget_all)
    if rows is not None:
        rows["month"], rows["day"] = month_all, day_all
    out = []
    for u in sorted(set(month) | set(budgets)):
        if not listed_user(f"USER#{u}"):
            continue
        its = {"Monthly": month.get(u), "Daily": day.get(u), "Hourly": hour.get(u)}
        lim, eff = limits_for(policy.read_item(budgets.get(u)), ug.get(u, []), its, warn, p["g"], pols, p["t"])
        spent = {r["period"]: r["spent"] or 0.0 for r in lim}  # totals count an unreadable record as 0 (r140), flagged per row
        worst = max((r["state"] for r in lim), key=WORST.index)
        out.append({"user": u, "groups": ug.get(u, []), "mtd": counters(month.get(u)), "today_usd": spent["Daily"], "hour_usd": spent["Hourly"],
                    "limits": lim, "rpm": eff["rpm"], "last_seen": (month.get(u) or {}).get("last_seen_at", {}).get("S"),
                    "state": "ok" if worst == "none" else worst,
                    # No BUDGET item: removed from var.users. Their month still counts, but they cannot call and their
                    # limits cannot be edited (404), so they are not someone who needs attention.
                    "removed": u not in budgets})
    return out


# ---------------- read endpoints ----------------

def overview():
    p = now_parts()
    # Today's rows come from users_view's own read (rows["day"]) and later days have none yet: query only the days before.
    sks = [f"DAY#{d}" for d in _days_of(p["month"]) if d < p["day"]]
    # The day queries do not depend on users_view: they are queued right behind its four reads and run while it works;
    # only this thread waits on them (no pooled task waits on the pool). If users_view fails, the queued ones are dropped.
    shared = {}
    try:
        users = users_view(shared, then=lambda: [_POOL.submit(scan_sk, sk) for sk in sks] if SK_INDEX else None, p=p)
    except Exception:
        for f in shared.get("then") or []:
            f.cancel()
        raise
    day_futs = shared["then"]
    month_rows = shared["month"]
    mtd = zero()
    for it in month_rows:
        if listed_user(it["pk"]["S"]):  # the same users as the Users page, so the KPI is the sum of its rows
            mtd = add(mtd, counters(it))
    days = {}
    # One pass over this month's DAY# rows serves both series: with the sk index that is one Query per day for every pk
    # (31 queries, in parallel), rather than one set for MODEL# rows and another for USER# rows.
    lo, hi = f"DAY#{p['month']}", f"DAY#{p['month']}\uffff"
    # With the index this reads every pk of each day (GROUP# rows are dropped below): 31 queries rather than 62 for
    # two kind-filtered passes, the cheaper side of that trade. Without it, one Scan with both prefixes in the filter.
    day_rows = [*shared["day"], *(it for f in day_futs for it in f.result())] if SK_INDEX else _paginate(
        "scan", FilterExpression="(begins_with(pk, :m) OR begins_with(pk, :u)) AND sk BETWEEN :lo AND :hi",
        ExpressionAttributeValues={":m": {"S": "MODEL#"}, ":u": {"S": "USER#"}, ":lo": {"S": lo}, ":hi": {"S": hi}})
    # Spend in the users' DAY rows with no MODEL# row behind it (web search, unpriced models, removed users; see
    # UNATTRIBUTED) is its own series, worked out per day the way the model mix does it for the month, so the daily bars
    # add up to the MTD KPI, which sums the same users' rows.
    user_day = collections.defaultdict(float)
    # One pass, one counters() per row: day_rows is every pk of every elapsed day of the month (a MODEL# row is never
    # a user row, so the two series never want the same item).
    for it in day_rows:
        pk, day = it["pk"]["S"], it["sk"]["S"][4:]
        if pk.startswith("MODEL#"):
            days.setdefault(day, {})[pk[6:]] = round(counters(it)["usd"], 6)
        elif listed_user(pk):
            user_day[day] += counters(it)["usd"]
    for day_key, total in user_day.items():
        d = days.setdefault(day_key, {})
        if (gap_d := round(total - sum(d.values()), 6)) > GAP_MIN:
            d[UNATTRIBUTED] = gap_d
    mix = []
    for it in month_rows:
        if it["pk"]["S"].startswith("MODEL#"):
            c = counters(it)  # once per row: it re-sums window_spend on every call
            mix.append({"model": it["pk"]["S"][6:], "usd": c["usd"], "requests": c["requests"]})
    # Spend on USER# rows only (web search, unpriced, removed users; see UNATTRIBUTED): its own slice, so the mix sums to MTD.
    gap = round(mtd["usd"] - sum(r["usd"] for r in mix), 6)
    if gap > GAP_MIN:
        mix.append({"model": UNATTRIBUTED, "usd": gap, "requests": max(0, mtd["requests"] - sum(r["requests"] for r in mix))})
    today = [u for u in users if u["today_usd"] > 0]
    elapsed = (p["t"] - calendar.timegm((p["g"].tm_year, p["g"].tm_mon, 1, 0, 0, 0))) / 86400
    dim = calendar.monthrange(p["g"].tm_year, p["g"].tm_mon)[1]
    org = policy.policy_items(ddb, TABLE).get("ORG", {})
    attention = [{"user": u["user"], "groups": u["groups"], "period": r["period"], "spent": r["spent"], "cap": r["cap"], "base": r["base"],
                  "pct": r["pct"], "state": r["state"], "unreadable": r["unreadable"]}
                 for u in users if not u["removed"] for r in u["limits"] if r["state"] in ("critical", "blocked") or r["unreadable"]]
    return {"month": p["month"], "today": p["day"],
            "kpis": {"mtd_usd": mtd["usd"], "today_usd": round(sum(u["today_usd"] for u in users), 6),
                     "projected_usd": round(mtd["usd"] / max(elapsed, 1 / 24) * dim, 2), "active_users_today": len(today),
                     "requests_mtd": mtd["requests"], "org_budget_usd": org.get("monthly_usd")},
            "daily": [{"date": d, "by_model": v} for d, v in sorted(days.items())],
            "model_mix": sorted(mix, key=lambda r: -r["usd"]),
            # a bad record first, then the closest to a cap
            "attention": sorted(attention, key=lambda r: (not r["unreadable"], -(r["pct"] or 0))), "generated_at": iso(p["t"])}


def _dates(frm, to, gran):
    try:
        t0 = calendar.timegm(time.strptime(frm, "%Y-%m-%d"))
    except ValueError:
        raise ApiError(400, "invalid date", field="from") from None
    try:
        t1 = calendar.timegm(time.strptime(to, "%Y-%m-%d"))
    except ValueError:
        raise ApiError(400, "invalid date", field="to") from None
    if t1 < t0:
        raise ApiError(400, "to is before from", field="to")
    if gran == "day":
        if (t1 - t0) / 86400 + 1 > 40:  # inclusive of both ends: at most 40 days, as the DAY rows' 40-day TTL
            raise ApiError(400, "day granularity covers at most 40 days", field="from")
        return [time.strftime("%Y-%m-%d", time.gmtime(t)) for t in range(t0, t1 + 1, 86400)]
    out, (y, m) = [], (int(frm[:4]), int(frm[5:7]))
    if (int(to[:4]) - y) * 12 + int(to[5:7]) - m >= 13:  # as for days: refuse an over-long range, never cut it short
        raise ApiError(400, "month granularity covers at most 13 months", field="from")
    while (y, m) <= (int(to[:4]), int(to[5:7])):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + (m == 12), m % 12 + 1)
    return out


def model_matches(model, wanted):
    """Model filter: exact, or the same id without the gateway target prefix ("kimi-k3" matches "ow/kimi-k3")."""
    if model == wanted:
        return True
    return model != UNATTRIBUTED and model.split("/", 1)[-1] == wanted.split("/", 1)[-1]


def usage(q):
    p = now_parts()
    gran = q.get("granularity", "day")
    if gran not in ("day", "month"):
        raise ApiError(400, "granularity must be day or month", field="granularity")
    group_by = q.get("group_by", "model")
    if group_by not in ("model", "user", "group", "tag"):
        raise ApiError(400, "group_by must be model, user, group or tag", field="group_by")
    frm = q.get("from") or p["month"] + "-01"
    to = q.get("to") or p["day"]
    for f, v in (("from", frm), ("to", to)):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise ApiError(400, "dates are YYYY-MM-DD", field=f)
    fu, fg, fm, ft = q.get("user"), q.get("group"), q.get("model"), q.get("tag")
    notes = []
    if (ft or group_by == "tag") and gran != "month":
        gran = "month"  # tag rollups are written per month only
        notes.append("Tag usage is rolled up per month, so this view is shown by month.")
    buckets = _dates(frm, to, gran)
    # Rows come from one family (USER#, GROUP# or TAG#), so a filter on another family cannot be applied to them.
    for bad, field in ((group_by == "user" and fg, "group"), (group_by == "user" and ft, "tag"),
                       (group_by in ("group", "tag") and fu, "user"), (group_by == "tag" and fg, "group"),
                       (group_by == "group" and ft, "tag"),
                       ):
        if bad:
            raise ApiError(400, f"group_by={group_by} cannot be combined with a {field} filter", field=field)
    if ft and fg:  # tag rows (TAG#) carry no group, whatever group_by is
        raise ApiError(400, "a tag filter cannot be combined with a group filter: tag usage is not split by group", field="group")
    if not TAG_DIMS and (group_by == "tag" or ft or q.get("dim")):
        raise ApiError(400, "tags are off for this gateway (tag_dimensions is empty in Terraform)", field="dim")
    dim = q.get("dim") or (TAG_DIMS[0] if TAG_DIMS else None)
    if TAG_DIMS and dim not in TAG_DIMS:
        raise ApiError(400, f"dim must be one of {', '.join(TAG_DIMS)}", field="dim")
    # Pick the row family that can answer the filters, then split by group_by.
    if ft or group_by == "tag":
        kind = "TAG#"
    elif fg or group_by == "group":
        kind = "GROUP#"
    else:
        kind = "USER#"
    if fu and kind != "USER#":
        notes.append("The user filter applies to user rows only; it is ignored when grouping or filtering by group or tag.")
    series, table = {}, {}
    # One scan for the whole range (a scan per bucket read the table once per day), bucketed here by sort key.
    sk_pfx = "DAY#" if gran == "day" else "MONTH#"
    wanted = set(buckets)
    # A filter on one name of this family (user=alice, group=eng, tag=team=a) is one base-table Query of that pk over
    # the range, not an index Query per bucket over every user's (or group's) rows.
    one = {"USER#": fu, "GROUP#": fg, "TAG#": ft}[kind]
    rows = ([] if not buckets else query(kind + one, sk_pfx + buckets[0], sk_pfx + buckets[-1]) if one
            else scan_range(kind, sk_pfx + buckets[0], sk_pfx + buckets[-1], [sk_pfx + b for b in buckets]))
    for it in rows:
        b = it["sk"]["S"][len(sk_pfx):]
        if b not in wanted:
            continue
        pk = it["pk"]["S"]
        if not pk.startswith(kind):
            continue
        name = pk[len(kind):]
        if kind == "USER#" and not listed_user(pk):
            continue
        if (kind == "USER#" and fu and name != fu) or (kind == "GROUP#" and fg and name != fg) or (kind == "TAG#" and ft and name != ft):
            continue
        if kind == "TAG#" and not ft and not name.startswith(dim + "="):
            continue  # every dimension sums to the full spend; adding them up would count it once per dimension
        if group_by == "model" or fm:
            parts = model_parts(it)  # with the (unattributed) remainder, so model views add up to the spend
        else:
            parts = {None: counters(it)}
        for model, c in parts.items():
            if fm and not model_matches(model, fm):
                continue
            key = model if group_by == "model" else name
            pt = series.setdefault(key, {})
            pt[b] = add(pt.get(b, zero()), c)
            table[key] = add(table.get(key, zero()), c)
    total = zero()
    for c in table.values():
        total = add(total, c)
    if kind == "GROUP#" and not fg:
        # A user in several groups is in each group's row, so the group rows can sum to more than the users' spend.
        notes.append("Each group counts all of its members' usage, so a user in several groups is counted in each and "
                     "the total can be higher than actual spend. Group by user or model for the actual total.")
    out_series = [{"key": k, "points": [{"t": b, **pts.get(b, zero())} for b in buckets]} for k, pts in sorted(series.items(), key=lambda kv: -table[kv[0]]["usd"])]
    return {"series": out_series, "table": [{"key": k, **c} for k, c in sorted(table.items(), key=lambda kv: -kv[1]["usd"])],
            "total": total, "notes": notes, "tag_dims": TAG_DIMS, "dim": dim}


AUDIT_SCAN_MAX = 20000  # rows a filtered audit search reads at most per request (a year of a busy trail is more)


def audit_rows(frm=None, to=None, limit=None, target=None, keep=None, stats=None):
    """Audit rows, newest first. A target's own copies (AUDIT#<target>) are read instead of the whole trail (AUDIT);
    every row has had its copy since the copies were introduced, before the first release of this sample. keep: a
    filter applied while paging, so `limit` counts matching rows and an older match is still found. A filtered search
    stops after AUDIT_SCAN_MAX rows, newest first, so a rare match cannot read the whole trail (and time out); stats,
    if given, gets complete=False then, so the UI can ask for a date range."""
    lo, hi = (frm or "0000"), ((to or "9999") + "\uffff")
    kw = {"KeyConditionExpression": "pk = :p AND sk BETWEEN :lo AND :hi", "ScanIndexForward": False,
          "ExpressionAttributeValues": {":p": {"S": f"AUDIT#{target}" if target else "AUDIT"}, ":lo": {"S": lo}, ":hi": {"S": hi}}}
    rows, read = [], 0
    while True:
        # Limit lets DynamoDB stop at the page size when nothing is filtered; a page also ends at 1 MB, so keep paging
        # (asking only for what is still missing) until the limit is reached or the trail ends.
        r = ddb.query(TableName=TABLE, **({**kw, "Limit": limit - len(rows)} if limit and not keep else kw))
        read += len(r.get("Items", []))
        for it in r.get("Items", []):
            row = _audit_row(it)
            if keep is None or keep(row):
                rows.append(row)
                if limit and len(rows) >= limit:
                    return rows
        if "LastEvaluatedKey" not in r:
            return rows
        if keep is not None and read >= AUDIT_SCAN_MAX:
            if stats is not None:
                stats["complete"] = False
            return rows
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def user_detail(u):
    p = now_parts()
    pols_f = _POOL.submit(policy.policy_items, ddb, TABLE)  # queued first: the views' other reads overlap it
    # Four GetItems plus the 30-day and audit Queries: all independent, so all at the same time.
    lo = time.strftime("DAY#%Y-%m-%d", time.gmtime(p["t"] - 29 * 86400))  # the same clock read as month, day and hour
    days_f = _POOL.submit(query, f"USER#{u}", lo, "DAY#9999")
    audit_f = _POOL.submit(audit_rows, limit=50, target=f"user:{u}")
    keys = (f"MONTH#{p['month']}", f"DAY#{p['day']}", f"HOUR#{p['hour']}", "BUDGET")
    # One round trip, strongly consistent: the page's version (from BUDGET) locks the next save.
    got = policy.batch_get(ddb, TABLE, [(f"USER#{u}", sk) for sk in keys], consistent=True)
    month, day, hour, budget = (got.get((f"USER#{u}", sk)) for sk in keys)
    if not (month or budget):
        # The two Queries were started for speed on the common case (a real user); stop them if they are still queued.
        # A name that does not exist costs at most these two small Queries, which only an admin or viewer can cause.
        days_f.cancel()
        audit_f.cancel()
        pols_f.cancel()  # a running one cannot fill the cache late: policy_items checks the cache generation
        raise ApiError(404, f"no usage or limits for {u}")
    pols = pols_f.result()
    warn = policy.warn_pct(pols.get("ORG"))
    groups = user_groups(only=u, budget_item=budget or {}).get(u, [])
    its = {"Monthly": month, "Daily": day, "Hourly": hour}
    pb = policy.plain(budget)
    b = policy.limits(pb)  # read once: the limit rows, the boost, its state and the override all use this copy
    lim, eff = limits_for(b, groups, its, warn, p["g"], pols, p["t"])  # one clock for the whole answer
    boost = boost_view(b, eff, p["t"])
    daily = [{"date": it["sk"]["S"][4:], "usd": c["usd"], "requests": c["requests"]}
             for it in days_f.result() for c in (counters(it),)]  # each row's counters summed once
    return {"user": u, "groups": groups, "limits": lim, "rpm": eff["rpm"], "boost": boost, "version": budget_version(budget),
            "removed": not budget,
            "boost_state": boost_state(pb, p["t"]),  # live, expired or unreadable boost attributes; End boost clears them
            "override": {k: b[a] for k, a in USER_ATTR.items() if b.get(a) is not None}, "models": eff["models"],
            "by_model_mtd": sorted(({"model": m, **c} for m, c in model_parts(month).items()), key=lambda r: -r["usd"]),
            "daily": daily, "audit": audit_f.result()}


def _group_has_members(g):
    """Whether any user is in group g (by policy.budget_groups' rule), for a group with no rollup, policy or env
    entry. The BUDGET rows are filtered and projected by DynamoDB, so only the few that name g come back, each with
    just its group fields, not every user's whole item."""
    kw = {"FilterExpression": "contains(token_groups, :g) OR #grp = :g", "ProjectionExpression": "pk, #grp, token_groups",
          "ExpressionAttributeNames": {"#grp": "group"}}
    if SK_INDEX:
        rows = _paginate("query", IndexName=SK_INDEX, KeyConditionExpression="sk = :s",
                         ExpressionAttributeValues={":s": {"S": "BUDGET"}, ":g": {"S": g}}, **kw)
    else:
        kw["FilterExpression"] = f"sk = :s AND ({kw['FilterExpression']})"
        rows = _paginate("scan", ExpressionAttributeValues={":s": {"S": "BUDGET"}, ":g": {"S": g}}, **kw)
    return any(g in policy.budget_groups(it) for it in rows if it["pk"]["S"].startswith("USER#"))


def groups_view(only=None):
    """Every group's row; with only, just that group's (one GetItem instead of the month's index Query)."""
    m = now_parts()["month"]
    pols_f = _POOL.submit(policy.policy_items, ddb, TABLE)  # queued first: the views' other reads overlap it
    if only is not None:
        it = get(f"GROUP#{only}", f"MONTH#{m}")
        pols = pols_f.result()
        rows = {only: it} if it else {}
        names = [only] if (it or f"GROUP#{only}" in pols or only in GROUP_MODELS or _group_has_members(only)) else []
    else:
        month_rows, budget_rows = _POOL.map(scan_sk, [f"MONTH#{m}", "BUDGET"], [None, "USER#"])
        rows = {it["pk"]["S"][6:]: it for it in month_rows if it["pk"]["S"].startswith("GROUP#")}
        # Groups the users are in (the token's, else the seeded one), so a group nobody has spent in this month, or
        # one with no models yet, still has a row an admin can edit.
        known = {g for gs in user_groups(budget_rows).values() for g in gs}
        pols = pols_f.result()
        names = sorted(set(rows) | {k[6:] for k in pols if k.startswith("GROUP#")} | set(GROUP_MODELS) | known)
    out = []
    for g in names:
        pol = pols.get(f"GROUP#{g}", {})
        eff = policy.resolve({}, pols, [g], ENV_LIMITS, GROUP_MODELS)
        out.append({"group": g, "members_seen": sorted(rows.get(g, {}).get("user_set", {}).get("SS", [])), "mtd": counters(rows.get(g)),
                    "policy": {k: v for k, v in pol.items() if k in ("monthly_usd", "daily_usd", "hourly_usd", "rpm", "models")},
                    "models_effective": eff["models"], "models_source": eff["models_source"][g], "version": pol.get("version"),
                    # "(no group)" only collects usage of users whose token has no groups: the interceptor resolves their
                    # limits from the org and deployment defaults, so a policy on it would never be enforced.
                    "editable": g != NO_GROUP})
    return out


def policy_view():
    pols_f = _POOL.submit(policy.policy_items, ddb, TABLE)  # queued first: the views' other reads overlap it
    budget_rows = budget_items()
    pols = pols_f.result()
    org = pols.get("ORG", {})
    boosts = []
    t = time.time()  # one clock read for the whole list, as the user pages
    for it in budget_rows:
        b = policy.read_item(it)
        if policy.boost_live(b, t):
            eff = policy.resolve(b, pols, policy.budget_groups(it), ENV_LIMITS, GROUP_MODELS, t)
            # Never None: boost_live and boost_view read the same clock. version: the user's limits version, so End
            # boost from this list is a 409 if the boost changed since.
            boosts.append({"user": it["pk"]["S"][5:], **boost_view(b, eff, t), "version": budget_version(it)})
    return {"org": {**{k: org.get(k) for k in ("monthly_usd", "daily_usd", "hourly_usd", "rpm", "block_message")},
                    "warn_pct": policy.warn_pct(org), "version": org.get("version")},
            "env_defaults": {"monthly_usd": ENV_LIMITS["Monthly"], "daily_usd": ENV_LIMITS["Daily"], "hourly_usd": ENV_LIMITS["Hourly"],
                             "rpm": ENV_LIMITS["rpm"]},
            "boosts": boosts}


def models_view():
    return {"models": [{**m, "description": m.get("description", ""), "price": PRICES.get(m["id"], {})} for m in MODEL_CATALOG],
            "aliases": MODEL_ALIASES}


def acc():
    global _acc
    # Adaptive retries: the Rate limits page lists and reads the gateway's limits, and the control plane throttles
    # bursts; the client slows down and retries instead of failing the page with a ThrottlingException.
    _acc = _acc or boto3.client("bedrock-agentcore-control", config=botocore.config.Config(
        max_pool_connections=16, retries={"mode": "adaptive", "max_attempts": 4}))
    return _acc


def _not_found(e):
    """True for an AWS error saying the resource is already gone (ResourceNotFoundException)."""
    return ((getattr(e, "response", None) or {}).get("Error") or {}).get("Code") == "ResourceNotFoundException"


RL_GETS = 8  # concurrent GetGatewayRateLimit calls: a steady stream the control plane takes, short enough for 30 s


def rate_limit_details(ids):
    """GetGatewayRateLimit for each id, in order, on the warm _POOL as a sliding window: at most RL_GETS in flight,
    and the next starts as soon as any one finishes, so one Get in retry backoff does not idle the other slots. A
    failure raises at once and starts no more; handle() waits for the ones still in flight."""
    out, pending, todo = [None] * len(ids), {}, iter(enumerate(ids))

    def start():
        for k, i in itertools.islice(todo, RL_GETS - len(pending)):
            pending[_POOL.submit(acc().get_gateway_rate_limit, gatewayIdentifier=GATEWAY_ID, rateLimitId=i)] = k
    start()
    while pending:
        done, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
        for f in done:
            k = pending.pop(f)
            try:
                out[k] = f.result()  # raises the Get's error
            except Exception as e:
                # Deleted between the List and this Get (a terraform apply or destroy meanwhile): it is gone, so it
                # is left out, not a failed page.
                if not _not_found(e):
                    raise
        start()
    return [d for d in out if d is not None]


def _limit_details():
    """Every gateway rate limit, read in full (the list summaries do not carry entries)."""
    if not GATEWAY_ID:
        return []
    ids = [s["rateLimitId"] for s in policy.paginate_token(acc().list_gateway_rate_limits, "nextToken",
                                                            ("rateLimits", "items"), gatewayIdentifier=GATEWAY_ID)]
    return rate_limit_details(ids)


def _identity_map():
    """IdP user id -> ledger user, as the interceptor sees it (the CONFIG/DIRECTORY item, cached a minute)."""
    if os.environ.get("DIRECTORY_FROM_TABLE") == "1":
        return policy.directory(ddb, TABLE, os.environ)[1]
    return IDENTITY_MAP


def gateway_limits():
    """The gateway's rate limits, one row per entry, each with the user it applies to (from identity_map; None for the
    "*" default). They are set in Terraform (rpm_default, tpm_default, rpm_overrides); the dashboard shows them."""
    idmap = _POOL.submit(_identity_map)
    details = _limit_details()
    ids = idmap.result()
    gw = []
    for d in details:
        # A limit with no entries (or none returned yet) still gets one row, so it shows.
        for e in d.get("entries") or [{}]:
            dims = e.get("dimensions") or {}
            dim, val = ("+".join(dims), "+".join(str(v) for v in dims.values())) if dims else ("", "")
            gw.append({"id": d["rateLimitId"], "description": d.get("description") or "", "dimension": dim, "value": val,
                       "user": None if val in ("", "*") else ids.get(val, val),
                       "requests_per_minute": _per_minute(e.get("requests")),
                       "tokens_per_minute": _per_minute(e.get("tokens")),
                       "status": d.get("status", ""), "created_at": str(d.get("createdAt") or "")})
    return gw


def _per_minute(rates):
    """The per-minute rate in a limit entry's requests or tokens list, or None (the list may be null or absent)."""
    return next((x.get("rate") for x in rates or [] if str(x.get("period") or "").lower() == "minute"), None)


def ratelimits_view():
    pols_f = _POOL.submit(policy.policy_items, ddb, TABLE)  # queued first: the views' other reads overlap it
    budgets = _POOL.submit(budget_items)  # overlaps the gateway calls below
    gw = gateway_limits()
    users = {it["pk"]["S"][5:]: policy.read_item(it).get("rpm") for it in budgets.result()}
    pols = pols_f.result()
    # default: the deployment's rpm (Terraform default_rpm), which applies to everyone no org, group or user rpm covers
    return {"interceptor": {"default": ENV_LIMITS["rpm"], "org": pols.get("ORG", {}).get("rpm"),
                            "groups": {k[6:]: v["rpm"] for k, v in pols.items() if k.startswith("GROUP#") and v.get("rpm") is not None},
                            "users": {u: v for u, v in users.items() if v is not None}},
            "gateway": gw}


# ---------------- writes ----------------

def _num(body, k, lo, hi, integer=False):
    if k not in body or body[k] is None:
        return None
    v = body[k]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not (lo <= v <= hi) or (integer and int(v) != v):
        raise ApiError(400, f"{k} must be a number from {lo} to {hi}", field=k)
    return int(v) if integer else float(v)


def _name(v, field):
    pat = GROUP_NAME if field == "group" else NAME
    if not isinstance(v, str) or not pat.match(v) or v != v.strip():
        raise ApiError(400, f"{field} has characters that are not allowed", field=field)
    return v


def _limits_body(body, allow_models=False, current=None):
    # Only the fields the caller sent: an absent field keeps its stored value, an explicit null clears it. current: the
    # stored values under the same keys, so a cap that is already 0 does not need a reason on every later save.
    vals = {k: _num(body, k, 0, 100000) for k in ("monthly_usd", "daily_usd", "hourly_usd") if k in body}
    # A cap between $0 and a cent would block like $0 (policy.at_cap compares at a micro-dollar) without the reason and
    # confirmation a $0 cap needs: caps are 0, or a cent or more.
    for k, v in vals.items():
        if v is not None and 0 < v < 0.01:
            raise ApiError(400, f"{k} must be 0 (blocks the user) or at least 0.01", field=k)
    if "rpm" in body:
        vals["rpm"] = _num(body, "rpm", 1, 100000, integer=True)
    if any(v == 0 and (current or {}).get(k) != 0 for k, v in vals.items()) and not (body.get("reason") or "").strip():
        raise ApiError(400, "a reason is required when a cap is set to 0", field="reason")
    if allow_models and "models" in body and body["models"] is not None:
        ids = {m["id"] for m in MODEL_CATALOG}
        # The interceptor rewrites an alias to its model before the allow-list check, so store the real model.
        ms = [MODEL_ALIASES.get(m, m) if isinstance(m, str) else m for m in body["models"]] if isinstance(body["models"], list) else body["models"]
        # A pattern is matched against model IDs (ow/<alias>), as Terraform's group_models check requires: one without
        # the ow/ prefix (kimi*) would save and then match nothing, refusing every call with no error to explain it.
        if not isinstance(ms, list) or not all(isinstance(m, str) and (m in ids or (m.endswith("*") and m.startswith("ow/"))) for m in ms):
            raise ApiError(400, "models must be catalog IDs or ow/<prefix>* (for example ow/kimi*)", field="models")
        vals["models"] = sorted(set(ms))
    elif allow_models and "models" in body:
        vals["models"] = None
    return vals


def _av(v):
    if v is None:
        return {"NULL": True}
    if isinstance(v, dict):  # a map written by hand on a POLICY item: keep it a map, not str(dict)
        return {"M": {k: _av(x) for k, x in v.items()}}
    if isinstance(v, bool):
        return {"BOOL": v}
    if isinstance(v, (int, float)):
        return {"N": repr(float(v)) if isinstance(v, float) else str(v)}
    if isinstance(v, list):
        return {"L": [_av(x) for x in v]}
    return {"S": str(v)}


def _audit_item(actor, action, target, before, after, reason):
    at = iso(time.time())
    return {"pk": {"S": "AUDIT"}, "sk": {"S": f"{at}#{secrets.token_hex(4)}"}, "at": {"S": at}, "actor": {"S": actor}, "action": {"S": action},
            "target": {"S": target}, "before": {"S": json.dumps(before, default=policy.shown_text)}, "after": {"S": json.dumps(after, default=policy.shown_text)},
            "reason": {"S": reason.strip() if isinstance(reason, str) else ""}, "ttl": {"N": str(int(time.time()) + 365 * 86400)}}


def _audit_puts(aud):
    """TransactWriteItems Puts for an audit row: the AUDIT row and its AUDIT#<target> copy, which user_detail reads
    with one bounded Query instead of filtering the whole AUDIT partition. Each Put is conditioned on the row not
    existing, so a write can only add an audit row, never replace one."""
    new = {"ConditionExpression": "attribute_not_exists(sk)"}
    return [{"Put": {"TableName": TABLE, "Item": aud, **new}},
            {"Put": {"TableName": TABLE, "Item": {**aud, "pk": {"S": f"AUDIT#{aud['target']['S']}"}}, **new}}]


def _audit_row(item):
    """An AUDIT item as an API row: the one mapping, used by the write responses and by audit_rows."""
    d = policy.read_item(item)
    return {"at": d.get("at"), "actor": d.get("actor"), "action": d.get("action"), "target": d.get("target"),
            "before": json.loads(d["before"]) if d.get("before") else None,
            "after": json.loads(d["after"]) if d.get("after") else None, "reason": d.get("reason") or None}


def _policy_view(raw, clean=True):
    """A POLICY item as replies show it: plain values without the item's key (pk, sk), cleaned as every reader cleans
    them (policy.limits), or as stored with clean=False (audit rows)."""
    d = {k: v for k, v in policy.plain(raw).items() if k not in ("pk", "sk")}
    return policy.limits(d) if clean else d


def put_policy(sk, body, actor, action, target, allow_models=False, delete=False):
    raw = get("POLICY", sk, consistent=True) or {}  # the version the write's lock compares against
    as_stored = _policy_view(raw, clean=False)
    cur = policy.limits(as_stored)
    if delete and not raw:  # before the version check: a stale client must learn there is nothing left, not retry a 409
        # (raw, not cur: an item holding only its key exists and can be removed)
        raise ApiError(404, "no policy to remove")
    # One rule for "has a version": the attribute is present, as DynamoDB's attribute_exists sees it (a hand-written
    # 0 or "" is a version, not an absent one).
    has_version = "version" in raw
    # Compared as the client saw it in a reply (policy.version_matches), so any stored version can be sent back.
    ok = policy.version_matches(raw["version"], body.get("version")) if has_version else not body.get("version")
    if not ok:  # no version stored: none, or "", means none
        raise ApiError(409, "changed by someone else", current=cur)
    # before and after are both the item as stored (plain, not cleaned), so an attribute this write leaves alone shows
    # the same on both sides of the audit row.
    before = as_stored or None
    if delete:
        after = None
        op = {"Delete": {"TableName": TABLE, "Key": {"pk": {"S": "POLICY"}, "sk": {"S": sk}}}}
    else:
        vals = _limits_body(body, allow_models, current=cur)
        if sk == "ORG":
            if "warn_pct" in body and body["warn_pct"] is None:
                vals["warn_pct"] = None  # an explicit null clears it, as for the caps: back to the 75/95 default
            elif "warn_pct" in body:
                w = body["warn_pct"]
                if not policy.valid_warn_pct(w):  # the same rule the read path (policy.warn_pct) applies
                    raise ApiError(400, "warn_pct must be two ascending numbers from 1 to 100", field="warn_pct")
                vals["warn_pct"] = [float(x) for x in w]
            if "block_message" in body:
                bm = body["block_message"]
                # opencode shows the refusal text as is: no terminal control sequences, line breaks or reversed text
                # in every capped developer's session (r196, threat model T-069).
                if bm is not None and (not isinstance(bm, str) or len(bm) > 500 or _CONTROL_OR_BIDI.search(bm)):
                    raise ApiError(400, "block_message must be text up to 500 characters, without control or direction "
                                   "characters", field="block_message")
                vals["block_message"] = bm or None
        if not vals:  # as put_user_limits: a save naming no field is a client error, not a new version and audit row
            raise ApiError(400, "nothing to change")
        # The audit row and the reply show what the item will hold: an attribute this write does not edit is stored
        # back as written (below), so it is reported as written too, not as the gate reads it (a hand-written rpm of
        # 2.5 stays 2.5, which the gate enforces as 2).
        keep = {k: v for k, v in (before or {}).items() if k not in vals and k != "version"}
        after = {**keep, **{k: v for k, v in vals.items() if v is not None}, "version": uuid.uuid4().hex}
        # Attributes this write does not edit are stored back exactly as they were (a hand-written binary or number
        # set included), never round-tripped through plain() and _av().
        item = {"pk": {"S": "POLICY"}, "sk": {"S": sk}, **{k: raw[k] if k in keep else _av(v) for k, v in after.items()}}
        op = {"Put": {"TableName": TABLE, "Item": item}}
    # No version yet: a new item, or one written by hand without one (policy.py allows that). attribute_not_exists on
    # version covers both, and the first save adds one, so a concurrent second save still fails.
    cond = {"ConditionExpression": "attribute_not_exists(version)"} if not has_version else {
        # the stored attribute as it is: a hand-written number or bool version is compared as stored, not a 500
        "ConditionExpression": "version = :v", "ExpressionAttributeValues": {":v": raw["version"]}}
    list(op.values())[0].update(cond)
    if delete and not has_version:  # attribute_not_exists(version) alone also holds once another delete removed it
        op["Delete"]["ConditionExpression"] = "attribute_exists(pk) AND attribute_not_exists(version)"
    aud = _audit_item(actor, action, target, before, after, body.get("reason"))
    def current():
        now = get("POLICY", sk, consistent=True)
        if delete and not now:  # lost a race with another delete: nothing is left (404), as for a stale client above
            raise ApiError(404, "no policy to remove")
        return _policy_view(now)
    _write_guarded([op, *_audit_puts(aud)], current)
    policy.clear_cache()
    return {"ok": True, "item": after, "audit": _audit_row(aud)}


def _unchanged(raw, attrs, names, values):
    """A ConditionExpression that holds only while attrs are still as read in raw (the BUDGET item has no version, so
    the read values are the version). Adds its placeholders to names and values. The item must still exist: a write
    must never re-create the BUDGET item of a user Terraform removed in between (an UpdateItem would)."""
    parts = ["attribute_exists(pk)"]
    for i, a in enumerate(attrs):
        names[f"#c{i}"] = a
        if a in (raw or {}):
            values[f":c{i}"] = raw[a]
            parts.append(f"#c{i} = :c{i}")
        else:
            parts.append(f"attribute_not_exists(#c{i})")
    return " AND ".join(parts)


def _write_guarded(items, current, conflict="changed by someone else"):
    """One transaction whose first item carries the optimistic-lock condition, followed by its audit puts. Only a
    failed condition on that first item means someone else saved first (409, with `current()` as the new state);
    throttling or a conflict on the audit puts is retryable (503, naming the cancellation reasons)."""
    try:
        ddb.transact_write_items(TransactItems=items)
    except ddb.exceptions.TransactionCanceledException as e:
        reasons = e.response.get("CancellationReasons") or []
        if reasons and reasons[0].get("Code") == "ConditionalCheckFailed":
            raise ApiError(409, conflict, current=current()) from None
        codes = ", ".join(r.get("Code", "?") for r in reasons if r.get("Code") != "None")
        raise ApiError(503, f"the save did not go through{f' ({codes})' if codes else ''}; try again") from None


def _write_budget(u, upd, aud, raw, gone_404=None):
    """Send a BUDGET update with its audit rows, conditioned on every attribute the version covers (EDITABLE: limits
    and boost) being as read in raw, so any admin edit in between is a 409 whichever field it touched. A changed item
    is a 409, and an item removed in between (Terraform dropped the user) is the same 404 as _known_budget. gone_404:
    the 404 message when the race was lost to a write that left no boost (End boost twice), as a lost policy delete."""
    names, values = dict(upd.get("ExpressionAttributeNames") or {}), dict(upd.get("ExpressionAttributeValues") or {})
    upd = {**upd, "ConditionExpression": _unchanged(raw, list(EDITABLE), names, values), "ExpressionAttributeNames": names}
    upd.pop("ExpressionAttributeValues", None)
    if values:
        upd["ExpressionAttributeValues"] = values
    def current():
        cur = get(f"USER#{u}", "BUDGET", consistent=True)  # the write that beat this one has only just landed
        if not cur:
            raise ApiError(404, not_a_user(u))
        if gone_404 and not any(a in cur for a in BOOST_ATTRS):
            raise ApiError(404, gone_404)
        return policy.read_item(cur)
    _write_guarded([{"Update": upd}, *_audit_puts(aud)], current)


def not_a_user(u):
    return f"{u} is not a user of this gateway (no BUDGET item; add them to var.users)"


def _known_budget(u):
    """The user's BUDGET item. Terraform seeds one for every user in var.users, so a name without one is not a user of
    this gateway (a typo, or someone removed): 404, rather than creating a phantom user with limits."""
    raw = get(f"USER#{u}", "BUDGET", consistent=True)  # writes lock on this item's version: read it as it is now
    if not raw:
        raise ApiError(404, not_a_user(u))
    return raw


BOOST_ATTRS = policy.BOOST_FIELDS
EDITABLE = (*USER_ATTR.values(), *BOOST_ATTRS)


def budget_version(raw):
    """A version of what an admin can edit on a BUDGET item (limits and boost), sent with the user page and back with
    a save. The interceptor's own write (token_groups) does not change it, so it never causes a 409."""
    # The stored attribute values, not the cleaned ones: two different hand edits that clean alike are two versions.
    return hashlib.sha256(json.dumps({a: (raw or {}).get(a) for a in EDITABLE}, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _check_version(u, raw, body):
    """409 when the save carries the version the admin loaded and the item has changed since (another admin saved).
    A save without a version (an older client, or a script) keeps the in-request check only."""
    want = body.get("version")
    if want is not None and want != budget_version(raw):
        raise ApiError(409, "changed by someone else", current=policy.read_item(raw))


def put_user_limits(u, body, actor):
    raw = _known_budget(u)
    _check_version(u, raw, body)
    cur = policy.read_item(raw)
    vals = _limits_body(body, current={k: cur.get(a) for k, a in USER_ATTR.items()})
    before = {k: cur.get(a) for k, a in USER_ATTR.items() if cur.get(a) is not None}
    sets, rems, names, values = [], [], {}, {}
    for i, (k, a) in enumerate(USER_ATTR.items()):
        if k not in body:
            continue
        names[f"#a{i}"] = a
        if vals[k] is None:
            rems.append(f"#a{i}")
        else:
            sets.append(f"#a{i} = :v{i}")
            values[f":v{i}"] = _av(vals[k])
    if not names:
        raise ApiError(400, "nothing to change")
    after = {**before, **{k: vals[k] for k in USER_ATTR if k in body and vals[k] is not None}}
    for k in USER_ATTR:
        if k in body and vals[k] is None:
            after.pop(k, None)
    expr = (" SET " + ", ".join(sets) if sets else "") + (" REMOVE " + ", ".join(rems) if rems else "")
    aud = _audit_item(actor, "set_user_limits", f"user:{u}", before or None, after or None, body.get("reason"))
    upd = {"TableName": TABLE, "Key": {"pk": {"S": f"USER#{u}"}, "sk": {"S": "BUDGET"}}, "UpdateExpression": expr.strip(),
           "ExpressionAttributeNames": names, "ExpressionAttributeValues": values}
    _write_budget(u, upd, aud, raw)
    return {"ok": True, "item": after, "audit": _audit_row(aud)}


def boost(u, body, actor, end=False):
    raw = _known_budget(u)
    # On the raw item: a hand-edited boost_usd that cleans to 0 is still a boost an admin must be able to end. Checked
    # before the version, so ending a boost that is already gone is a 404 whatever version the page held.
    if end and not any(a in raw for a in BOOST_ATTRS):
        raise ApiError(404, "no boost to end")
    _check_version(u, raw, body)
    pr = policy.plain(raw)
    cur = policy.limits(pr)
    before = ({a.removeprefix("boost_"): cur.get(a) for a in BOOST_ATTRS} if cur.get("boost_usd") else  # limits voids a hand edit
              # a hand-edited boost cleans to nothing: the audit keeps what was there as text (End and a new grant alike)
              {a: _typed_text(raw[a]) for a in BOOST_ATTRS if a in raw} or None)
    key = {"pk": {"S": f"USER#{u}"}, "sk": {"S": "BUDGET"}}
    if end:
        upd = {"TableName": TABLE, "Key": key, "UpdateExpression": "REMOVE " + ", ".join(BOOST_ATTRS)}
        after, action = None, "end_boost"
    else:
        usd = _num(body, "usd", 0.01, 100000)
        hours = _num(body, "hours", 1, 720, integer=True)
        if usd is None or hours is None:
            raise ApiError(400, "usd and hours are required", field="usd" if usd is None else "hours")
        period = body.get("period")
        if period not in policy.PERIODS:
            raise ApiError(400, "period must be Monthly, Daily or Hourly", field="period")
        # A boost raises an existing cap; on a period without one it would be recorded and change nothing. A $0 cap
        # blocks the user on purpose (Block user, typed confirmation): a boost must not lift it by the back door.
        eff = policy.resolve(cur, policy.policy_items(ddb, TABLE), user_groups(only=u, budget_item=raw).get(u, []), ENV_LIMITS, GROUP_MODELS)
        c = eff["caps"][period]
        if c["cap"] is None:
            raise ApiError(400, f"{u} has no {period.lower()} cap, so a {period.lower()} boost would have no effect", field="period")
        if c["base"] <= 0:
            raise ApiError(400, f"{u}'s {period.lower()} cap is $0, which blocks them; raise the cap instead of boosting it", field="period")
        reason = (body.get("reason") or "").strip()
        if not reason:
            raise ApiError(400, "a reason is required for a boost", field="reason")
        until = int(time.time()) + hours * 3600
        after = {"usd": usd, "period": period, "until": until, "reason": reason}
        upd = {"TableName": TABLE, "Key": key, "UpdateExpression": "SET boost_usd = :u, boost_period = :p, boost_until = :t, boost_reason = :r",
               "ExpressionAttributeValues": {":u": _av(usd), ":p": {"S": period}, ":t": {"N": str(until)}, ":r": {"S": reason}}}
        action = "grant_boost"
        # The no-cap check above read the user's own cap for this period: the write condition below (every editable
        # attribute) makes an admin removing it meanwhile a 409, not a stored boost that changes nothing. Caps from
        # group or org policy are separate items; a boost on a cap that goes away later shows as not in effect.
    aud = _audit_item(actor, action, f"user:{u}", before, after, body.get("reason"))
    _write_budget(u, upd, aud, raw, gone_404="no boost to end" if end else None)
    return {"ok": True, "item": after, "audit": _audit_row(aud)}


def _typed_text(av):
    """A raw DynamoDB attribute value as short text for a repair's audit row, its type shown because a repair is about
    the type a hand edit wrote: '<N> 5', '<S> "x"'; a binary as policy.BINARY_SHOWN. Plain values in other audit rows
    go through policy.shown_text."""
    if not isinstance(av, dict) or len(av) != 1:
        return repr(av)[:100]
    (t, v), = av.items()
    return policy.BINARY_SHOWN if t == "B" else f"<{t}> {json.dumps(v, default=policy.shown_text)[:100]}"


def repair_window(u, body, actor):
    """Remove the spend attributes of one of the user's current windows that the gateway cannot read (a hand edit:
    not a number, or negative), so a capped user is no longer refused and an uncapped one stops failing its search
    charges. Only those attributes go, and only if they are still unreadable when the write lands (the condition);
    usage_sync sets model_spend_usd again on its next run, and web search spend restarts at 0. Audited like every write."""
    period = body.get("period")
    if period not in policy.PERIODS:
        raise ApiError(400, "period must be Monthly, Daily or Hourly", field="period")
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise ApiError(400, "a reason is required to repair a usage record", field="reason")
    sk = policy.window_sk(policy.PERIOD_KIND[period], now_parts()["g"])  # the key every view reads for this period
    known = _POOL.submit(_known_budget, u)  # independent reads, side by side
    item = get(f"USER#{u}", sk, consistent=True)  # a hand edit made a moment ago must read as it is now
    known.result()
    bad = policy.bad_attrs(item)
    if not bad:
        raise ApiError(409, f"{u}'s {period.lower()} usage record can be read; nothing to repair")
    names = {f"#a{i}": a for i, a in enumerate(bad)}
    # Still unreadable when the write lands: never removes a value the gateway or usage_sync has since rewritten.
    # attribute_exists(pk): an item that expired (ttl) meanwhile is not re-created by the REMOVE.
    cond = "attribute_exists(pk) AND " + " AND ".join(f"(NOT attribute_type({n}, :n) OR {n} < :z)" for n in names)
    upd = {"TableName": TABLE, "Key": {"pk": {"S": f"USER#{u}"}, "sk": {"S": sk}}, "UpdateExpression": "REMOVE " + ", ".join(names),
           "ConditionExpression": cond, "ExpressionAttributeNames": names, "ExpressionAttributeValues": {":n": {"S": "N"}, ":z": {"N": "0"}}}
    # The audit keeps what was there as text: a hand edit can be any DynamoDB type, binary included.
    before = {a: _typed_text(item[a]) for a in bad}
    aud = _audit_item(actor, "repair_usage_record", f"user:{u}", {"window": sk, **before}, {"window": sk, "removed": bad}, reason)
    _write_guarded([{"Update": upd}, *_audit_puts(aud)], lambda: {"window": sk, "unreadable": policy.bad_attrs(get(f"USER#{u}", sk, consistent=True))},
                   conflict="the record changed meanwhile; reload and check it again")
    return {"ok": True, "item": {"window": sk, "removed": bad}, "audit": _audit_row(aud)}


# ---------------- router ----------------

def handle(method, path, q, body, ident):
    """ident: {"email", "sub", "groups", "role"}. Returns (status, json-able body). Every read the request started on
    _POOL has finished when it returns, whether the view succeeded or raised."""
    try:
        return _handle(method, path, q, body, ident)
    finally:
        _POOL.drain()


def _handle(method, path, q, body, ident):
    # The email is what an admin reads; the sub is what cannot be changed by the user (r194).
    actor = (f"{ident['email']} ({ident['sub']})" if ident.get("email") and ident.get("sub")
             else ident.get("email") or ident.get("sub") or "unknown")
    # Read POLICY items fresh on every dashboard request: the 60 s cache is per container, so after a save on another
    # container this one would serve the old version (a false 409 on the next edit) and the old model lists.
    policy.clear_cache()
    # Split first, then decode each segment: the UI sends names through encodeURIComponent (dev%40example.com), and an
    # encoded "/" stays inside its segment, where _name's character check rejects it.
    parts = [urllib.parse.unquote(p) for p in path[len("/api/v2/"):].split("/") if p]
    if method == "GET":
        if parts == ["me"]:
            # tag_dims: so the UI knows up front which tag dimensions exist ([] = tags off), even when a usage query fails
            # sign_out: only the ALB mode has a session to end (/logout); Function URL callers sign in with IAM.
            return 200, {**{k: ident.get(k) for k in ("email", "sub", "groups", "role")}, "tag_dims": TAG_DIMS,
                         "sign_out": bool(os.environ.get("ALB_ARN"))}
        if parts == ["overview"]:
            return 200, overview()
        if parts == ["usage"]:
            return 200, usage(q)
        if parts == ["users"]:
            return 200, {"users": users_view()}
        if len(parts) == 2 and parts[0] == "users":
            return 200, user_detail(_name(parts[1], "user"))
        if parts == ["groups"]:
            return 200, {"groups": groups_view()}
        if len(parts) == 2 and parts[0] == "groups":
            g = _name(parts[1], "group")
            row = next(iter(groups_view(only=g)), None)
            if not row:
                raise ApiError(404, f"no group {g}")
            return 200, row
        if parts == ["policy"]:
            return 200, policy_view()
        if parts == ["models"]:
            return 200, models_view()
        if parts == ["ratelimits"]:
            return 200, ratelimits_view()
        if parts == ["audit"]:
            t = q.get("target") or ""
            # Text with a ":" (or "org") is a full target ("user:alice", "group:eng") and is one Query on its
            # AUDIT#<target> copies, so "user:al" matches nothing; text without one ("alice") is a substring search over
            # the whole trail (API.md).
            exact = ":" in t or t == "org"
            actor_q = (q.get("actor") or "").lower()  # the filter text; `actor` stays the signed-in caller

            def keep(r):
                return (not actor_q or actor_q in (r["actor"] or "").lower()) and (exact or not t or t.lower() in (r["target"] or "").lower())
            # The newest AUDIT_PAGE rows that match: filters apply while paging, so an older match is still found.
            frm, to = q.get("from") or None, q.get("to") or None
            for f, v in (("from", frm), ("to", to)):
                if v is not None and not AUDIT_TIME.fullmatch(v):
                    raise ApiError(400, "use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ", field=f)
            if frm and to and frm > to + "\uffff":
                raise ApiError(400, "from is after to", field="from")
            # No filter left to apply while paging: let DynamoDB stop at the page size instead of reading 1 MB pages.
            filtered = bool(actor_q) or (bool(t) and not exact)
            stats = {}
            rows = audit_rows(frm, to, limit=AUDIT_PAGE, target=t if exact else None, keep=keep if filtered else None, stats=stats)
            # complete: false when a filtered search stopped at AUDIT_SCAN_MAX rows; narrow it with from/to.
            return 200, {"rows": rows, "complete": stats.get("complete", True)}
        raise ApiError(404, "not found")
    if ident.get("role") != "admin":
        raise ApiError(403, "read-only role")
    # Every write may carry a reason for the audit log: checked once here, so no handler meets a number or a list.
    reason = (body or {}).get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > REASON_MAX):
        raise ApiError(400, f"reason must be text up to {REASON_MAX} characters", field="reason")
    if parts == ["policy", "org"] and method == "PUT":
        return 200, put_policy("ORG", body, actor, "set_org_policy", "org")
    if len(parts) == 3 and parts[:2] == ["policy", "groups"]:
        if parts[2] == NO_GROUP:
            raise ApiError(400, "(no group) is not an IdP group: users without groups get the org policy, so edit that instead", field="group")
        g = _name(parts[2], "group")
        if method == "PUT":
            return 200, put_policy(f"GROUP#{g}", body, actor, "set_group_policy", f"group:{g}", allow_models=True)
        if method == "DELETE":
            return 200, put_policy(f"GROUP#{g}", body, actor, "delete_group_policy", f"group:{g}", delete=True)
    if len(parts) == 3 and parts[0] == "users":
        u = _name(parts[1], "user")
        if parts[2] == "limits" and method == "PUT":
            return 200, put_user_limits(u, body, actor)
        if parts[2] == "boost" and method in ("POST", "DELETE"):
            return 200, boost(u, body, actor, end=method == "DELETE")
        if parts[2] == "repair" and method == "POST":
            return 200, repair_window(u, body, actor)
    raise ApiError(404, "not found")
