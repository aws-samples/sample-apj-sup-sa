# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Scenario grid for policy resolution and model matching, checked against an independent oracle.

Covers every combination of: user override per window (absent / 0 / value), group caps for 0-2 groups (absent / 0 /
low / high), org cap (absent / value), env default (None / value), boost (none / live / expired, on each period).
"""
import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lambda"))
import policy as p  # noqa: E402

PER = {"Monthly": ("budget_usd", "monthly_usd"), "Daily": ("daily_usd", "daily_usd"), "Hourly": ("hourly_usd", "hourly_usd")}
NOW = 1_000_000.0


def oracle(user, pol, groups, env, period):
    ukey, pkey = PER[period]
    if user.get(ukey) is not None:
        cap, src = user[ukey], "user"
    else:
        g = [(pol[f"GROUP#{x}"][pkey], f"group:{x}") for x in groups if pol.get(f"GROUP#{x}", {}).get(pkey) is not None]
        if g:
            cap, src = max(g, key=lambda t: t[0])
        elif pol.get("ORG", {}).get(pkey) is not None:
            cap, src = pol["ORG"][pkey], "org"
        else:
            cap, src = env.get(period), ("default" if env.get(period) is not None else None)
    live = user.get("boost_usd") and (user.get("boost_until") or 0) > NOW and user.get("boost_period", "Monthly") == period
    # A boost on an uncapped period does nothing, and neither does one on a $0 cap (a block; r162).
    boost = float(user["boost_usd"]) if live and cap is not None and cap > 0 else 0.0
    return (cap + boost if cap is not None else None), src, boost


USERS = [None, 0.0, 7.5]
GROUPCAPS = [None, 0.0, 3.0, 9.0]
GROUPSETS = [[], ["a"], ["a", "b"]]
ORGS = [None, 4.0]
ENVS = [None, 50.0]
BOOSTS = [None, ("Daily", NOW + 60), ("Daily", NOW - 60), ("Monthly", NOW + 60), ("Hourly", NOW + 60)]

CASES = []
for period in PER:
    for u, gs, ga, gb, o, e, b in itertools.product(USERS, GROUPSETS, GROUPCAPS, GROUPCAPS, ORGS, ENVS, BOOSTS):
        if not gs and (ga is not None or gb is not None):
            continue
        if len(gs) < 2 and gb is not None:
            continue
        CASES.append((period, u, tuple(gs), ga, gb, o, e, b))


@pytest.mark.parametrize("period,u,gs,ga,gb,o,e,b", CASES)
def test_resolve_matches_oracle(period, u, gs, ga, gb, o, e, b):
    ukey, pkey = PER[period]
    user = {} if u is None else {ukey: u}
    if b:
        user.update({"boost_usd": 2.0, "boost_period": b[0], "boost_until": b[1]})
    pol = {}
    if o is not None:
        pol["ORG"] = {pkey: o}
    for g, c in zip(gs, (ga, gb)):
        pol[f"GROUP#{g}"] = {} if c is None else {pkey: c}
    env = {"Monthly": None, "Daily": None, "Hourly": None, "rpm": None, period: e}
    r = p.resolve(user, pol, list(gs), env, {}, now=NOW)["caps"][period]
    assert (r["cap"], r["source"], r["boost"]) == oracle(user, pol, list(gs), env, period)


MODELS = ["ow/glm-5.3", "ow/kimi-k3", "ow/deepseek-v3.2", "acme-chat-1", "acme-chat-2", "acme-mini-1",
          "ow/", "", "OW/glm-5.3", "ow/glm-5.3 ", "ow/glm-5.3*", "acme-", "cc/acme-chat-1", "ow/../acme-x"]
PATTERNS = [[], ["*"], ["ow/*"], ["acme-*"], ["ow/glm-5.3"], ["ow/glm-5.3", "acme-*"], ["acme-chat-1"],
            ["ow/kimi-*"], ["c*"], ["ow/glm-5.3*"], ["OW/*"], [""]]


def match_oracle(model, pats):
    for pat in pats:
        if pat.endswith("*"):
            if model[:len(pat) - 1] == pat[:-1]:
                return True
        elif model == pat:
            return True
    return False


@pytest.mark.parametrize("model,pats", list(itertools.product(MODELS, PATTERNS)))
def test_model_allowed_matches_oracle(model, pats):
    assert p.model_allowed(model, pats) == match_oracle(model, pats)


@pytest.mark.parametrize("groups,expect", [
    ([], []), (["dev"], ["acme-*"]), (["ow"], ["ow/*"]), (["dev", "ow"], ["acme-*", "ow/*"]),
    (["ow", "dev"], ["ow/*", "acme-*"]), (["dev", "dev"], ["acme-*"]), (["unknown"], []),
])
def test_models_union_is_ordered_and_deduplicated(groups, expect):
    assert p.resolve({}, {}, groups, {}, {"dev": ["acme-*"], "ow": ["ow/*"]})["models"] == expect


@pytest.mark.parametrize("rpm_user,rpm_group,rpm_org,rpm_env,expect", [
    (None, None, None, None, (None, None)), (5, 50, 30, 10, (5, "user")), (None, 50, 30, 10, (50, "group:a")),
    (None, None, 30, 10, (30, "org")), (None, None, None, 10, (10, "default")), (0, 50, 30, 10, (0, "user")),
    (2.9, None, None, None, (2, "user")),
])
def test_rpm_resolution(rpm_user, rpm_group, rpm_org, rpm_env, expect):
    pol = {"GROUP#a": {} if rpm_group is None else {"rpm": rpm_group}}
    if rpm_org is not None:
        pol["ORG"] = {"rpm": rpm_org}
    r = p.resolve({} if rpm_user is None else {"rpm": rpm_user}, pol, ["a"], {"rpm": rpm_env}, {})
    assert (r["rpm"]["value"], r["rpm"]["source"]) == expect
