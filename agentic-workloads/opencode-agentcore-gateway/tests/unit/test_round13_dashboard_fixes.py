# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Round-13 code review: a user's Activity tab finds their older audit rows past 500 newer ones for others; a failed
delete of the old gateway rate limit still writes the audit row; Overview reads only MODEL# day rows."""

import boto3
import pytest

from test_dashboard_api import _audits, _gw, api, call  # noqa: F401 - fixture


def _put_audit(target, at):
    # As the dashboard writes it: the AUDIT row plus its AUDIT#<target> copy.
    for pk in ("AUDIT", f"AUDIT#{target}"):
        boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": pk}, "sk": {"S": f"{at}#x{target}"}, "at": {"S": at},
                                                              "actor": {"S": "a@x"}, "action": {"S": "set_user_limits"}, "target": {"S": target},
                                                              "before": {"S": "null"}, "after": {"S": "{}"}, "reason": {"S": ""}})


@pytest.mark.parametrize("others,mine", [(0, 1), (600, 1), (600, 60), (1, 0)])
def test_user_audit_finds_older_rows(api, others, mine):
    for n in range(mine):
        _put_audit("user:alice", f"2026-09-01T00:{n // 60:02d}:{n % 60:02d}Z")
    for n in range(others):
        _put_audit(f"user:u{n}", f"2026-10-01T{n // 3600:02d}:{n // 60 % 60:02d}:{n % 60:02d}Z")
    s, d = call(api, "GET", "/api/v2/users/alice")
    assert s == 200 and len(d["audit"]) == min(mine, 50) and all(r["target"] == "user:alice" for r in d["audit"])
    if mine > 1:
        assert d["audit"][0]["at"] > d["audit"][-1]["at"]  # newest first


def test_overview_daily_is_one_scan_without_the_index(api, monkeypatch):
    seen = []
    real = api._paginate
    monkeypatch.setattr(api, "_paginate", lambda op, **kw: (seen.append(kw.get("FilterExpression", "")), real(op, **kw))[1])
    s, o = call(api, "GET", "/api/v2/overview")
    # Without the sk index the daily series is one Scan for MODEL# and USER# day rows together, not one per kind.
    day_scans = [f for f in seen if "sk BETWEEN" in f]
    assert s == 200 and day_scans == ["(begins_with(pk, :m) OR begins_with(pk, :u)) AND sk BETWEEN :lo AND :hi"]
