# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""terraform/helpers/acgw.py: lookups follow every page."""
import importlib.util
import json
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "terraform" / "helpers" / "acgw.py"


class Pages:
    def __init__(self, pages):
        self.pages, self.created, self.deleted = pages, [], []

    def list_gateway_rate_limits(self, gatewayIdentifier, nextToken=None):  # noqa: N803
        i = int(nextToken or 0)
        out = {"rateLimits": self.pages[i]}
        if i + 1 < len(self.pages):
            out["nextToken"] = str(i + 1)
        return out

    def get_gateway_rate_limit(self, gatewayIdentifier, rateLimitId):  # noqa: N803
        found = next((r for p in self.pages for r in p if r["rateLimitId"] == rateLimitId), None)
        if found is None:  # as the service: a deleted limit is a ResourceNotFoundException
            import botocore.exceptions
            raise botocore.exceptions.ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "GetGatewayRateLimit")
        return {"status": "ACTIVE", **found}

    def create_gateway_rate_limit(self, **kw):
        self.created.append(kw["description"])
        return {"rateLimitId": "new"}

    def delete_gateway_rate_limit(self, gatewayIdentifier, rateLimitId):  # noqa: N803
        self.deleted.append(rateLimitId)
        self.pages = [[r for r in p if r["rateLimitId"] != rateLimitId] for p in self.pages]

    def update_gateway_rate_limit(self, **kw):
        self.created.append(("update", kw["rateLimitId"]))


@pytest.fixture()
def acgw(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    spec = importlib.util.spec_from_file_location("acgw_under_test", str(SRC))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ratelimit_found_on_a_later_page_is_not_duplicated_and_is_deleted(acgw, monkeypatch):
    users = [{"rateLimitId": f"u{i}", "description": f"ocgw-dashboard user u{i}"} for i in range(30)]
    ctl = Pages([users[:10], users[10:20], users[20:] + [{"rateLimitId": "global", "description": "ocgw per-user",
                                                          "dimensionKeys": ["k"], "entries": []}]])
    monkeypatch.setattr(acgw, "CTL", ctl)
    acgw.ratelimit_ensure("gw", '{"description": "ocgw per-user", "dimensionKeys": ["k"], "entries": []}')
    assert ctl.created == []  # found on the last page: neither created again nor updated (unchanged)
    acgw.ratelimit_delete("gw", "ocgw per-user")
    assert ctl.deleted == ["global"]


def _env(acgw, monkeypatch, tmp_path, mantle):
    monkeypatch.setattr(acgw, "mantle", mantle)
    monkeypatch.setattr(acgw.time, "sleep", lambda s: None)
    monkeypatch.setattr(acgw.S, "client", lambda name: type("Sts", (), {"get_caller_identity": lambda self: {"Account": "123"}})())
    monkeypatch.setattr(acgw, "Path", lambda *_a: tmp_path / "helpers" / "acgw.py")
    return tmp_path / "build" / ".mantle-projects-123-us-east-1.json"


def test_a_rate_limit_deleted_mid_lookup_is_skipped(acgw, monkeypatch):
    # r65: the round-64 not-found fix was in the dashboard only; here the Get raised and failed the apply.
    import botocore.exceptions
    items = [{"rateLimitId": f"u{i}"} for i in range(20)] + [{"rateLimitId": "global"}]

    class Ctl(Pages):
        def get_gateway_rate_limit(self, gatewayIdentifier, rateLimitId):  # noqa: N803
            if rateLimitId == "u3":
                raise botocore.exceptions.ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "Get")
            return {"description": "ocgw per-user" if rateLimitId == "global" else f"user {rateLimitId}"}
    monkeypatch.setattr(acgw, "CTL", Ctl([items]))
    assert list(acgw._ratelimits("gw", "ocgw per-user")) == ["global"]


def test_deleting_a_rate_limit_that_is_already_gone_succeeds(acgw, monkeypatch):
    # r66: a retried delete whose first try took effect got ResourceNotFound and failed the destroy.
    import botocore.exceptions

    class Ctl(Pages):
        def delete_gateway_rate_limit(self, gatewayIdentifier, rateLimitId):  # noqa: N803
            self.pages = [[]]  # it is gone: the first try took effect
            raise botocore.exceptions.ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "Delete")
    monkeypatch.setattr(acgw, "CTL", Ctl([[{"rateLimitId": "g", "description": "ocgw per-user"}]]))
    acgw.ratelimit_delete("gw", "ocgw per-user")


def test_a_failed_rate_limit_get_cancels_the_gets_not_started(acgw, monkeypatch):
    # r66: a pool left running waited for every queued Get of the page.
    import time as _t
    calls = []

    class Ctl(Pages):
        def get_gateway_rate_limit(self, gatewayIdentifier, rateLimitId):  # noqa: N803
            calls.append(rateLimitId)
            if rateLimitId == "u0":
                raise RuntimeError("throttled")
            _t.sleep(0.02)
            return {"description": "x"}
    monkeypatch.setattr(acgw, "CTL", Ctl([[{"rateLimitId": f"u{i}"} for i in range(200)]]))
    with pytest.raises(RuntimeError, match="throttled"):
        acgw._ratelimits("gw", "ocgw per-user")
    assert len(calls) < 50


def test_mantle_retries_a_request_that_never_left_but_not_an_unclear_create(acgw, monkeypatch):
    import requests
    import urllib3.exceptions as u3
    calls = []

    class R:
        status_code, text = 200, ""

        def json(self):
            return {"ok": 1}

    def req(method, url, **k):
        calls.append(method)
        if len(calls) == 1:
            raise requests.ConnectionError(u3.MaxRetryError(None, "/", u3.NameResolutionError("h", None, OSError("dns"))))
        if method == "POST" and len(calls) == 2:
            raise requests.ReadTimeout("lost reply")
        return R()
    monkeypatch.setattr(acgw.requests, "request", req)
    monkeypatch.setattr(acgw.time, "sleep", lambda s: None)
    monkeypatch.setattr(acgw.S, "get_credentials", lambda: type("C", (), {"get_frozen_credentials": lambda self: type("F", (), {"access_key": "a", "secret_key": "s", "token": None})()})())
    with pytest.raises(acgw.UnclearWrite):
        acgw.mantle("POST", "/v1/organization/projects", {}, idempotent=False)
    assert calls == ["POST", "POST"]  # the DNS failure was retried; the lost reply was not


def test_a_project_without_a_usable_created_at_is_never_preferred(acgw):
    ps = [{"id": "a", "name": "n", "status": "active"}, {"id": "b", "name": "n", "status": "active", "created_at": "x"},
          {"id": "c", "name": "n", "status": "active", "created_at": 5}]
    assert acgw._oldest(ps, "n", None)["id"] == "c"


def test_ratelimit_delete_removes_every_limit_with_the_description(acgw, monkeypatch):
    # r69: only the first match was deleted, so a duplicate "*" limit kept capping users at the old rates.
    ctl = Pages([[{"rateLimitId": "a", "description": "d"}, {"rateLimitId": "x", "description": "other"}],
                 [{"rateLimitId": "b", "description": "d"}]])
    monkeypatch.setattr(acgw, "CTL", ctl)
    acgw.ratelimit_delete("gw", "d")
    assert ctl.deleted == ["a", "b"]


class _OneLimit(Pages):
    """One limit per dimension-key set, as the gateway enforces."""

    def __init__(self, limit=None):
        super().__init__([[{"rateLimitId": limit["rateLimitId"]}]] if limit else [[]])
        self.limit, self.updated, self.made = limit, [], []

    def get_gateway_rate_limit(self, gatewayIdentifier, rateLimitId):  # noqa: N803
        return {"status": "ACTIVE", **self.limit} if self.limit else {"rateLimitId": rateLimitId, "status": "ACTIVE"}

    def update_gateway_rate_limit(self, **kw):
        self.updated.append(kw)
        self.limit = {**self.limit, **kw, "updatedAt": len(self.updated)}  # the service moves updatedAt on an update

    def create_gateway_rate_limit(self, **kw):
        self.made.append(kw)
        return {"rateLimitId": "new"}


def _spec(desc="ocgw per user #new", rpm=60):
    k = "$.context.jwt.username"
    return {"description": desc, "dimensionKeys": [k], "entries": [{"dimensions": {k: "*"}, "requests": [{"rate": rpm, "period": "minute"}]}]}


def test_ratelimit_ensure_updates_the_one_limit_in_place(acgw, monkeypatch):
    # r69 live: a second limit on the same keys is refused, so a new spec updates the existing limit.
    import json
    k = "$.context.jwt.username"
    ctl = _OneLimit({"rateLimitId": "rl", "description": "ocgw per user #old", "dimensionKeys": [k], "entries": _spec(rpm=30)["entries"]})
    monkeypatch.setattr(acgw, "CTL", ctl)
    acgw.ratelimit_ensure("gw", json.dumps(_spec()))
    assert ctl.made == [] and len(ctl.updated) == 1 and ctl.updated[0]["entries"][0]["requests"][0]["rate"] == 60
    acgw.ratelimit_delete("gw", "ocgw per user #old")  # the previous version's destroy step: no longer its limit
    assert ctl.deleted == []


def test_entries_in_another_order_are_not_an_update(acgw, monkeypatch):
    # r72: a list comparison treated the service's own entry order as a change, so every apply updated the limit.
    import json
    k = "$.context.jwt.username"
    spec = _spec(desc="d")
    spec["entries"].append({"dimensions": {k: "bob"}, "requests": [{"rate": 5.0, "period": "minute"}]})
    ctl = _OneLimit({"rateLimitId": "rl", "description": "d", "dimensionKeys": [k], "entries": list(reversed(spec["entries"]))})
    monkeypatch.setattr(acgw, "CTL", ctl)
    acgw.ratelimit_ensure("gw", json.dumps(spec))
    assert ctl.updated == [] and ctl.made == []


def test_an_integer_rate_from_terraform_matches_the_services_float(acgw, monkeypatch):
    # r73: jsonencode renders 60, the service returns 60.0; compared as strings that was an update on every re-apply.
    import json
    k = "$.context.jwt.username"
    spec = {"description": "d", "dimensionKeys": [k], "entries": [{"dimensions": {k: "*"}, "requests": [{"rate": 60, "period": "minute"}]}]}
    ctl = _OneLimit({"rateLimitId": "rl", "description": "d", "dimensionKeys": [k], "status": "ACTIVE",
                     "entries": [{"dimensions": {k: "*"}, "requests": [{"rate": 60.0, "period": "minute"}]}]})
    monkeypatch.setattr(acgw, "CTL", ctl)
    acgw.ratelimit_ensure("gw", json.dumps(spec))
    assert ctl.updated == []


def test_an_update_names_the_entries_terraform_does_not_set(acgw, monkeypatch, capsys):
    import json
    k = "$.context.jwt.username"
    ctl = _OneLimit({"rateLimitId": "rl", "description": "old", "dimensionKeys": [k],
                     "entries": _spec()["entries"] + [{"dimensions": {k: "bob"}, "requests": [{"rate": 5.0, "period": "minute"}]}]})
    monkeypatch.setattr(acgw, "CTL", ctl)
    acgw.ratelimit_ensure("gw", json.dumps(_spec()))
    assert len(ctl.updated) == 1 and "bob" in capsys.readouterr().err


def test_ratelimit_ensure_waits_for_active_and_fails_on_a_failed_update(acgw, monkeypatch):
    # r74: create and update finish asynchronously; an UPDATE_FAILED left the apply green with the old entries live.
    import json
    k = "$.context.jwt.username"
    ctl = _OneLimit({"rateLimitId": "rl", "description": "old", "dimensionKeys": [k], "entries": []})
    states = iter(["UPDATING", "UPDATE_FAILED"])
    real_get = ctl.get_gateway_rate_limit
    ctl.get_gateway_rate_limit = lambda **kw: {**real_get(**kw), "status": next(states, "UPDATE_FAILED")} if ctl.updated else real_get(**kw)
    monkeypatch.setattr(acgw, "CTL", ctl)
    monkeypatch.setattr(acgw.time, "sleep", lambda s: None)
    with pytest.raises(SystemExit, match="UPDATE_FAILED"):
        acgw.ratelimit_ensure("gw", json.dumps(_spec()))


def test_two_entries_for_one_value_are_not_taken_for_one(acgw):
    k = "$.context.jwt.username"
    one = [{"dimensions": {k: "x"}, "requests": [{"rate": 5, "period": "minute"}]}]
    two = one + [{"dimensions": {k: "x"}, "requests": [{"rate": 9.0, "period": "MINUTE"}]}]
    assert acgw._normal(one) != acgw._normal(two) and acgw._normal(two) == acgw._normal(list(reversed(two)))



def test_wait_ignores_the_old_active_straight_after_an_update(acgw):
    # r75: the first poll after UpdateGatewayRateLimit can still be the pre-update ACTIVE; the update then failed.
    import agentcore_wait
    seq = iter([{"status": "ACTIVE", "updatedAt": 1}, {"status": "UPDATING", "updatedAt": 1}, {"status": "UPDATE_FAILED", "updatedAt": 2}])
    with pytest.raises(RuntimeError, match="UPDATE_FAILED"):
        agentcore_wait.wait_active(lambda: next(seq), "rate limit rl", before={"status": "ACTIVE", "updatedAt": 1}, sleep=lambda t: None)


def test_wait_lets_a_failed_limit_be_updated_again(acgw):
    # r75: right after re-updating an UPDATE_FAILED limit the old UPDATE_FAILED is still reported for a moment.
    import agentcore_wait
    seq = iter([{"status": "UPDATE_FAILED", "updatedAt": 1}, {"status": "UPDATING", "updatedAt": 1}, {"status": "ACTIVE", "updatedAt": 2}])
    assert agentcore_wait.wait_active(lambda: next(seq), "x", grace=3, before={"status": "UPDATE_FAILED", "updatedAt": 1},
                                      sleep=lambda t: None)["status"] == "ACTIVE"


@pytest.mark.parametrize("gone", [None, {"status": "DELETING"}])
def test_wait_fails_at_once_when_the_limit_goes(acgw, gone):
    import agentcore_wait
    polls = []
    with pytest.raises(RuntimeError, match="gone|deleted"):
        agentcore_wait.wait_active(lambda: polls.append(1) or gone, "x", sleep=lambda t: None)
    assert len(polls) == 1


def test_an_update_failed_limit_matching_the_spec_is_updated_again(acgw, monkeypatch):
    # r75: an unchanged limit returned at once, so an UPDATE_FAILED one with the requested entries passed as done.
    import json
    k = "$.context.jwt.username"
    ctl = _OneLimit({"rateLimitId": "rl", "description": "ocgw per user #new", "dimensionKeys": [k], "entries": _spec()["entries"],
                     "status": "UPDATE_FAILED"})
    real_update = ctl.update_gateway_rate_limit
    ctl.update_gateway_rate_limit = lambda **kw: (real_update(**kw), ctl.limit.update(status="ACTIVE", updatedAt=9))[0]
    monkeypatch.setattr(acgw, "CTL", ctl)
    monkeypatch.setattr(acgw.time, "sleep", lambda s: None)
    acgw.ratelimit_ensure("gw", json.dumps(_spec()))
    assert len(ctl.updated) == 1


def test_a_replacement_with_an_unchanged_spec_is_not_deleted_by_the_old_instance(acgw, monkeypatch):
    # r76: -replace gave the new instance the old description, so the old instance's destroy deleted the live limit.
    import json
    k = "$.context.jwt.username"
    ctl = _OneLimit({"rateLimitId": "rl", "description": "d ~old11111", "dimensionKeys": [k], "entries": _spec()["entries"]})
    monkeypatch.setattr(acgw, "CTL", ctl)
    monkeypatch.setattr(acgw.time, "sleep", lambda s: None)
    monkeypatch.setenv("OCGW_RL_OWNER", "new22222-aaaa")
    acgw.ratelimit_ensure("gw", json.dumps({**_spec(), "description": "d"}))
    assert ctl.updated[-1]["description"] == "d ~new22222"
    monkeypatch.setattr(acgw, "_ratelimits", lambda gw, *descs: {"rl": acgw._get_limit(gw, "rl")} if ctl.limit["description"] in descs else {})
    monkeypatch.setenv("OCGW_RL_OWNER", "old11111-bbbb")
    acgw.ratelimit_delete("gw", "d")
    assert ctl.deleted == []


def test_a_create_failed_limit_is_deleted_and_created_again(acgw, monkeypatch):
    # r76: an update cannot revive a limit that never came up, and a create is refused while its keys are taken.
    import json
    k = "$.context.jwt.username"
    ctl = _OneLimit({"rateLimitId": "rl", "description": "d", "dimensionKeys": [k], "entries": [], "status": "CREATE_FAILED"})
    real_get = ctl.get_gateway_rate_limit

    def get(**kw):
        if kw["rateLimitId"] == "rl" and ctl.deleted:
            import botocore.exceptions
            raise botocore.exceptions.ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "Get")
        return real_get(**kw)
    ctl.get_gateway_rate_limit = get
    real_create = ctl.create_gateway_rate_limit
    ctl.create_gateway_rate_limit = lambda **kw: (setattr(ctl, "limit", {**kw, "rateLimitId": "new", "status": "ACTIVE"}), real_create(**kw))[1]
    monkeypatch.setattr(acgw, "CTL", ctl)
    monkeypatch.setattr(acgw.time, "sleep", lambda s: None)
    acgw.ratelimit_ensure("gw", json.dumps(_spec(desc="d")))
    assert ctl.deleted == ["rl"] and len(ctl.made) == 1 and ctl.updated == []


def test_a_limit_updating_that_then_fails_is_updated_again_not_an_exit(acgw, monkeypatch):
    # r77: the in-flight wait raised on UPDATE_FAILED, so the re-apply the docstring promises never ran.
    import json
    k = "$.context.jwt.username"
    ctl = _OneLimit({"rateLimitId": "rl", "description": "d", "dimensionKeys": [k], "entries": _spec()["entries"], "status": "UPDATING"})
    seq = iter(["UPDATING", "UPDATE_FAILED"])
    real_get = ctl.get_gateway_rate_limit
    ctl.get_gateway_rate_limit = lambda **kw: {**real_get(**kw), "status": next(seq, ctl.limit["status"])}
    real_update = ctl.update_gateway_rate_limit
    ctl.update_gateway_rate_limit = lambda **kw: (real_update(**kw), ctl.limit.update(status="ACTIVE", updatedAt=9))[0]
    monkeypatch.setattr(acgw, "CTL", ctl)
    monkeypatch.setattr(acgw.time, "sleep", lambda s: None)
    acgw.ratelimit_ensure("gw", json.dumps(_spec(desc="d")))
    assert len(ctl.updated) == 1


def test_a_delete_failed_limit_is_deleted_again_and_created(acgw, monkeypatch):
    # r77: a DELETE_FAILED limit went down the update path, which the service refuses, on every apply.
    import json
    import botocore.exceptions
    k = "$.context.jwt.username"
    ctl = _OneLimit({"rateLimitId": "rl", "description": "d", "dimensionKeys": [k], "entries": [], "status": "DELETE_FAILED"})
    polls = {"n": 0}
    real_get = ctl.get_gateway_rate_limit

    def get(**kw):
        if kw["rateLimitId"] == "rl" and ctl.deleted:
            polls["n"] += 1
            if polls["n"] > 1:  # the old DELETE_FAILED is still reported once right after the retried delete
                raise botocore.exceptions.ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "Get")
        return real_get(**kw)
    ctl.get_gateway_rate_limit = get
    real_create = ctl.create_gateway_rate_limit
    ctl.create_gateway_rate_limit = lambda **kw: (setattr(ctl, "limit", {**kw, "rateLimitId": "new", "status": "ACTIVE"}), real_create(**kw))[1]
    monkeypatch.setattr(acgw, "CTL", ctl)
    monkeypatch.setattr(acgw.time, "sleep", lambda s: None)
    acgw.ratelimit_ensure("gw", json.dumps(_spec(desc="d")))
    assert ctl.deleted == ["rl"] and len(ctl.made) == 1 and ctl.updated == []


def test_destroy_lists_the_limits_once_for_both_descriptions(acgw, monkeypatch):
    # r77: the tagged and the bare description were looked up in two full listing passes.
    calls = []
    monkeypatch.setattr(acgw, "_ratelimits", lambda gw, *descs: calls.append(set(descs)) or {})
    monkeypatch.setenv("OCGW_RL_OWNER", "abcdefgh-1234")
    acgw.ratelimit_delete("gw", "d")
    assert calls == [{"d", "d ~abcdefgh"}]


def test_wait_settled_returns_a_failed_status_instead_of_raising(acgw):
    from agentcore_wait import wait_settled
    seq = iter([{"status": "CREATING"}, {"status": "CREATE_FAILED"}])
    assert wait_settled(lambda: next(seq), "x", sleep=lambda s: None) == {"status": "CREATE_FAILED"}
    assert wait_settled(lambda: None, "x", sleep=lambda s: None) is None


def _nf():
    import botocore.exceptions
    return botocore.exceptions.ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "Get")


def test_a_retried_destroy_of_a_delete_failed_limit_waits_out_the_stale_status(acgw, monkeypatch):
    # r78: ratelimit_delete gave no grace, so the first read of the old DELETE_FAILED failed every retried destroy.
    reads = {"n": 0}

    def get(**kw):
        reads["n"] += 1
        if reads["n"] > 2:  # 1: the status check; 2: the stale DELETE_FAILED right after the delete; then gone
            raise _nf()
        return {"rateLimitId": "rl", "status": "DELETE_FAILED", "description": "d"}
    ctl = _OneLimit({"rateLimitId": "rl", "description": "d"})
    ctl.get_gateway_rate_limit = get
    monkeypatch.setattr(acgw, "CTL", ctl)
    monkeypatch.setattr(acgw.time, "sleep", lambda s: None)
    monkeypatch.setattr(acgw, "_ratelimits", lambda gw, *descs: {"rl": acgw._get_limit(gw, "rl")})
    acgw.ratelimit_delete("gw", "d")
    assert ctl.deleted == ["rl"]


def test_a_limit_already_deleting_is_waited_out_not_deleted_again(acgw, monkeypatch):
    # r78: the second delete of a DELETING limit was refused with a non-NotFound error and failed the destroy.
    reads = {"n": 0}

    def get(**kw):
        reads["n"] += 1
        if reads["n"] > 2:
            raise _nf()
        return {"rateLimitId": "rl", "status": "DELETING", "description": "d"}
    ctl = _OneLimit({"rateLimitId": "rl", "description": "d"})
    ctl.get_gateway_rate_limit = get
    monkeypatch.setattr(acgw, "CTL", ctl)
    monkeypatch.setattr(acgw.time, "sleep", lambda s: None)
    monkeypatch.setattr(acgw, "_ratelimits", lambda gw, *descs: {"rl": acgw._get_limit(gw, "rl")})
    acgw.ratelimit_delete("gw", "d")
    assert ctl.deleted == []


def test_wait_gone_gives_a_stale_delete_failed_its_grace_only_when_it_was_the_old_status(acgw):
    import pytest as _pytest
    from agentcore_wait import wait_gone_many
    seq = iter([{"status": "DELETE_FAILED"}, None])
    wait_gone_many({"x": lambda: next(seq)}, sleep=lambda s: None, was={"x": "DELETE_FAILED"})
    with _pytest.raises(RuntimeError, match="DELETE_FAILED"):
        wait_gone_many({"x": lambda: {"status": "DELETE_FAILED"}}, sleep=lambda s: None, was={"x": "ACTIVE"})


def test_get_or_none_swallows_only_not_found(acgw):
    import pytest as _pytest
    from agentcore_wait import get_or_none

    def nf(**kw):
        raise _nf()

    def boom(**kw):
        raise ValueError("other")
    assert get_or_none(nf) is None
    with _pytest.raises(ValueError):
        get_or_none(boom)


def test_destroy_waits_out_an_in_flight_change_before_deleting(acgw, monkeypatch):
    # r79: a limit still UPDATING (an apply that timed out) was deleted at once; the service refuses that.
    ctl = Pages([[{"rateLimitId": "a", "description": "d"}]])
    reads = iter(["UPDATING", "UPDATING", "ACTIVE"])
    real_get = ctl.get_gateway_rate_limit
    ctl.get_gateway_rate_limit = lambda **kw: {**real_get(**kw), "status": next(reads, "ACTIVE")}
    real_delete, left = ctl.delete_gateway_rate_limit, []
    ctl.delete_gateway_rate_limit = lambda **kw: left.extend(reads) or real_delete(**kw)  # reads not yet made
    monkeypatch.setattr(acgw, "CTL", ctl)
    monkeypatch.setattr("time.sleep", lambda s: None)
    acgw.ratelimit_delete("gw", "d")
    assert ctl.deleted == ["a"] and left == []  # the delete came only once the limit read ACTIVE


def test_a_summary_with_null_dimension_keys_is_read_in_full(acgw, monkeypatch):
    # r79: sorted(None) raised TypeError when a summary carried "dimensionKeys": null.
    ctl = Pages([[{"rateLimitId": "a", "dimensionKeys": None}]])
    real_get = ctl.get_gateway_rate_limit
    ctl.get_gateway_rate_limit = lambda **kw: {**real_get(**kw), "dimensionKeys": ["user"]}
    monkeypatch.setattr(acgw, "CTL", ctl)
    assert acgw._limit_with_keys("gw", ["user"])["rateLimitId"] == "a"


def test_is_not_found_tolerates_an_exception_without_a_response(acgw):
    from agentcore_wait import get_or_none, is_not_found

    class Odd(Exception):
        response = None

    assert not is_not_found(Odd()) and not is_not_found(ValueError())

    def boom():
        raise Odd("the real error")
    with pytest.raises(Odd, match="the real error"):
        get_or_none(boom)


def test_wait_settled_many_polls_all_in_one_loop(acgw):
    from agentcore_wait import wait_settled_many
    a, b, sleeps = iter(["UPDATING", "ACTIVE"]), iter(["CREATING", "CREATING", "CREATE_FAILED"]), []
    out = wait_settled_many({"a": lambda: {"status": next(a)}, "b": lambda: {"status": next(b)}, "c": lambda: None},
                            sleep=sleeps.append)
    assert out == {"a": {"status": "ACTIVE"}, "b": {"status": "CREATE_FAILED"}, "c": None} and len(sleeps) == 2
    with pytest.raises(RuntimeError, match="rules x still changing after 10 s"):
        wait_settled_many({"x": lambda: {"status": "UPDATING"}}, noun="rules ", polls=2, sleep=lambda s: None)


def test_a_summary_with_a_null_description_is_read_and_deleted(acgw, monkeypatch):
    # r80: description null was neither matched nor read in full, so the destroy left the limit behind.
    ctl = Pages([[{"rateLimitId": "a", "description": None}]])
    real_get = ctl.get_gateway_rate_limit
    ctl.get_gateway_rate_limit = lambda **kw: {**real_get(**kw), "description": "d"}
    monkeypatch.setattr(acgw, "CTL", ctl)
    acgw.ratelimit_delete("gw", "d")
    assert ctl.deleted == ["a"]


def test_destroy_reads_each_limit_once(acgw, monkeypatch):
    # r80: a limit already read in full to learn its description was read again for its status.
    ctl = Pages([[{"rateLimitId": "a"}]])
    real_get, gets = ctl.get_gateway_rate_limit, []
    ctl.get_gateway_rate_limit = lambda **kw: gets.append(kw["rateLimitId"]) or {**real_get(**kw), "description": "d"}
    monkeypatch.setattr(acgw, "CTL", ctl)
    monkeypatch.setattr("time.sleep", lambda s: None)
    acgw.ratelimit_delete("gw", "d")
    assert ctl.deleted == ["a"] and gets == ["a", "a"]  # one read to match, one by the wait that finds it gone


def test_a_limit_renamed_by_the_replacement_while_it_settled_is_left_alone(acgw, monkeypatch):
    # r80: the old instance's destroy deleted the limit its replacement had just taken over: no "*" limit was left.
    ctl = Pages([[{"rateLimitId": "a", "description": "d"}]])
    reads = iter([{"status": "UPDATING", "description": "d"}])
    real_get = ctl.get_gateway_rate_limit
    ctl.get_gateway_rate_limit = lambda **kw: {**real_get(**kw), **next(reads, {"status": "ACTIVE", "description": "d ~newowner"})}
    monkeypatch.setattr(acgw, "CTL", ctl)
    monkeypatch.setattr("time.sleep", lambda s: None)
    acgw.ratelimit_delete("gw", "d")
    assert ctl.deleted == []


def test_destroy_goes_by_the_get_description_not_the_lagging_summary(acgw, monkeypatch):
    # r81: an ACTIVE limit whose summary still showed the old description was deleted, although its Get showed the
    # replacement's: every user was left without the "*" limit.
    ctl = Pages([[{"rateLimitId": "a", "description": "d"}]])
    real_get = ctl.get_gateway_rate_limit
    ctl.get_gateway_rate_limit = lambda **kw: {**real_get(**kw), "description": "d ~newowner"}
    monkeypatch.setattr(acgw, "CTL", ctl)
    acgw.ratelimit_delete("gw", "d")
    assert ctl.deleted == []


def test_a_rate_limit_delete_refused_for_a_change_in_progress_settles_and_retries(acgw, monkeypatch):
    # r81: a ConflictException from DeleteGatewayRateLimit ended the destroy with a traceback.
    import botocore.exceptions
    ctl = Pages([[{"rateLimitId": "a", "description": "d"}]])
    real_delete, tries = ctl.delete_gateway_rate_limit, []

    def delete(**kw):
        tries.append(1)
        if len(tries) == 1:
            raise botocore.exceptions.ClientError({"Error": {"Code": "ConflictException"}}, "DeleteGatewayRateLimit")
        real_delete(**kw)
    ctl.delete_gateway_rate_limit = delete
    monkeypatch.setattr(acgw, "CTL", ctl)
    monkeypatch.setattr("time.sleep", lambda s: None)
    acgw.ratelimit_delete("gw", "d")
    assert ctl.deleted == ["a"] and len(tries) == 2


def test_a_rate_limit_delete_refused_twice_stops_with_a_message(acgw, monkeypatch):
    import botocore.exceptions
    ctl = Pages([[{"rateLimitId": "a", "description": "d"}]])

    def delete(**kw):
        raise botocore.exceptions.ClientError({"Error": {"Code": "ConflictException", "Message": "busy"}}, "DeleteGatewayRateLimit")
    ctl.delete_gateway_rate_limit = delete
    monkeypatch.setattr(acgw, "CTL", ctl)
    sleeps = []
    monkeypatch.setattr("time.sleep", sleeps.append)
    with pytest.raises(SystemExit, match="rate limit a could not be deleted"):
        acgw.ratelimit_delete("gw", "d")
    # r90: each resend waits until a read shows the conflicting change has ended; with no updatedAt and no UPDATING
    # seen, that is the stale window (6 polls of 3 s) per try, 5 resends in all, then the message.
    assert set(sleeps) == {3} and len(sleeps) == 5 * 6


def test_destroy_reads_a_limit_whose_summary_still_shows_another_description(acgw, monkeypatch):
    # r82: the summary's lagging description (an older version of it) kept the limit from being read, and destroy
    # left it behind. r83: only summaries of this limit's own versions (same base) or without a description are read.
    ctl = Pages([[{"rateLimitId": "a", "description": "d #oldhash1"}, {"rateLimitId": "x", "description": "another stack"}]])
    real_get = ctl.get_gateway_rate_limit
    gets = []
    ctl.get_gateway_rate_limit = lambda **kw: gets.append(kw["rateLimitId"]) or {**real_get(**kw), "description": "d"}
    monkeypatch.setattr(acgw, "CTL", ctl)
    acgw.ratelimit_delete("gw", "d")
    assert ctl.deleted == ["a"] and "x" not in gets


def test_a_limit_with_null_dimension_keys_is_found_by_its_entries(acgw, monkeypatch):
    # r82: dimensionKeys null on List and Get made ensure create a second limit for keys already taken.
    k = "$.context.jwt.username"
    ctl = _OneLimit({"rateLimitId": "rl", "description": "old", "dimensionKeys": None, "entries": _spec()["entries"]})
    monkeypatch.setattr(acgw, "CTL", ctl)
    monkeypatch.setattr(acgw.time, "sleep", lambda s: None)
    assert acgw._limit_with_keys("gw", [k])["rateLimitId"] == "rl"
    acgw.ratelimit_ensure("gw", json.dumps(_spec()))
    assert ctl.made == [] and ctl.updated


def test_an_update_refused_for_a_change_in_progress_stops_with_a_rerun_message(acgw, monkeypatch):
    # r82: UpdateGatewayRateLimit's ConflictException ended the apply with a traceback. r83: a resend was judged
    # against the read from before the conflicting change, so it now stops and asks for the run to be repeated.
    import botocore.exceptions
    ctl = _OneLimit({"rateLimitId": "rl", "description": "old", "dimensionKeys": ["$.context.jwt.username"],
                     "entries": _spec()["entries"]})
    real_update, tries = ctl.update_gateway_rate_limit, []

    def update(**kw):
        tries.append(1)
        if len(tries) == 1:
            raise botocore.exceptions.ClientError({"Error": {"Code": "ConflictException"}}, "UpdateGatewayRateLimit")
        real_update(**kw)
    ctl.update_gateway_rate_limit = update
    monkeypatch.setattr(acgw, "CTL", ctl)
    monkeypatch.setattr(acgw.time, "sleep", lambda s: None)
    with pytest.raises(SystemExit, match="could not be updated.*run terraform again"):
        acgw.ratelimit_ensure("gw", json.dumps(_spec()))
    assert len(tries) == 1


def test_destroy_settles_its_in_flight_limits_in_one_loop(acgw, monkeypatch):
    # r82: each matching limit was settled and waited out on its own, one after another.
    ctl = Pages([[{"rateLimitId": "a", "description": "d"}, {"rateLimitId": "b", "description": "d"}]])
    real_get, reads, sleeps = ctl.get_gateway_rate_limit, {"a": 0, "b": 0}, []

    def get(**kw):
        reads[kw["rateLimitId"]] += 1
        return {**real_get(**kw), "status": "UPDATING" if reads[kw["rateLimitId"]] <= 3 else "ACTIVE"}
    ctl.get_gateway_rate_limit = get
    monkeypatch.setattr(acgw, "CTL", ctl)
    monkeypatch.setattr("time.sleep", sleeps.append)
    acgw.ratelimit_delete("gw", "d")
    assert sorted(ctl.deleted) == ["a", "b"] and len(sleeps) == 2  # one settle loop (2 polls), not one per limit


def test_a_dead_limit_without_keys_or_entries_is_found_by_its_description_and_repaired(acgw, monkeypatch):
    # r83: dimensionKeys null and no entries (a limit that never came up) matched nothing, so every apply's create
    # was refused for keys still taken and the dead limit was never deleted.
    ctl = _OneLimit({"rateLimitId": "rl", "description": "ocgw per user #old ~abc", "dimensionKeys": None,
                     "entries": None, "status": "CREATE_FAILED"})
    import botocore.exceptions
    real_get, deleted = ctl.get_gateway_rate_limit, []

    def get(**kw):
        if deleted and kw["rateLimitId"] == "rl":
            raise botocore.exceptions.ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "Get")
        return real_get(**kw)
    ctl.get_gateway_rate_limit = get
    ctl.delete_gateway_rate_limit = lambda **kw: deleted.append(kw["rateLimitId"]) or setattr(ctl, "limit", None)
    monkeypatch.setattr(acgw, "CTL", ctl)
    monkeypatch.setattr(acgw.time, "sleep", lambda s: None)
    real_ensure_get = acgw._limit_with_keys
    assert real_ensure_get("gw", ["$.context.jwt.username"], "ocgw per user")["rateLimitId"] == "rl"
    acgw.ratelimit_ensure("gw", json.dumps(_spec()))
    assert deleted == ["rl"] and len(ctl.made) == 1


def test_destroy_settles_refused_deletes_together(acgw, monkeypatch):
    # r83: each delete refused with ConflictException settled on its own before the next delete was sent.
    import botocore.exceptions
    ctl = Pages([[{"rateLimitId": "a", "description": "d"}, {"rateLimitId": "b", "description": "d"}]])
    real_delete, tries, sleeps = ctl.delete_gateway_rate_limit, [], []
    reads = {"a": 0, "b": 0}
    real_get = ctl.get_gateway_rate_limit

    def get(**kw):
        reads[kw["rateLimitId"]] += 1
        return {**real_get(**kw), "status": "UPDATING" if 2 <= reads[kw["rateLimitId"]] <= 3 else "ACTIVE"}

    def delete(**kw):
        tries.append(kw["rateLimitId"])
        if tries.count(kw["rateLimitId"]) == 1:
            raise botocore.exceptions.ClientError({"Error": {"Code": "ConflictException"}}, "DeleteGatewayRateLimit")
        real_delete(**kw)
    ctl.get_gateway_rate_limit, ctl.delete_gateway_rate_limit = get, delete
    monkeypatch.setattr(acgw, "CTL", ctl)
    monkeypatch.setattr("time.sleep", sleeps.append)
    acgw.ratelimit_delete("gw", "d")
    assert tries == ["a", "b", "a", "b"] and sorted(ctl.deleted) == ["a", "b"] and len(sleeps) == 3  # one pause, one 2-poll settle
    assert set(sleeps) == {3} and len(sleeps) <= 4  # r90: the UPDATING read shows the change, so no fixed wait


def test_the_create_failed_repair_logs_a_delete_only_when_one_was_sent(acgw, monkeypatch, capsys):
    # r83: "rate limit deleted (CREATE_FAILED)" was logged when the settle found the limit already deleting.
    import botocore.exceptions
    ctl = _OneLimit({"rateLimitId": "rl", "description": "x", "dimensionKeys": ["$.context.jwt.username"],
                     "entries": _spec()["entries"], "status": "CREATE_FAILED"})
    reads = []

    def get(**kw):  # the settle after the refused delete finds it DELETING (deleted elsewhere), then it is gone
        if kw["rateLimitId"] == "new":
            return {"rateLimitId": "new", "status": "ACTIVE"}
        reads.append(1)
        return {"rateLimitId": "rl", "status": "DELETING"} if len(reads) < 2 else _nf_raise()

    def _nf_raise():
        raise botocore.exceptions.ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "Get")

    def delete(**kw):
        raise botocore.exceptions.ClientError({"Error": {"Code": "ConflictException"}}, "DeleteGatewayRateLimit")
    monkeypatch.setattr(acgw, "_limit_with_keys", lambda gw, keys, base=None: {**ctl.limit})
    ctl.get_gateway_rate_limit, ctl.delete_gateway_rate_limit = get, delete
    monkeypatch.setattr(acgw, "CTL", ctl)
    monkeypatch.setattr(acgw.time, "sleep", lambda s: None)
    acgw.ratelimit_ensure("gw", json.dumps(_spec()))
    assert "rate limit deleted" not in capsys.readouterr().err


@pytest.mark.parametrize("body,method,want", [(b"", "POST", {}), (b"  ", "POST", {}), (b"OK", "POST", {}), (b'{"a": 1}', "GET", {"a": 1})])
def test_mantle_takes_an_empty_or_plain_success_reply_of_a_write_as_done(acgw, monkeypatch, body, method, want):
    # r84: an archive answering 204 with no body raised JSONDecodeError and failed the destroy after it took effect.
    class Reply:
        status_code, content = 204 if not body.strip() else 200, body
        text = body.decode()

        def json(self):
            import json as _j
            return _j.loads(self.content)
    monkeypatch.setattr(acgw.requests, "request", lambda *a, **k: Reply())
    monkeypatch.setattr(acgw.S, "get_credentials", lambda: type("C", (), {"get_frozen_credentials": lambda self: type("F", (), {"access_key": "AKIDEXAMPLE", "secret_key": "x", "token": None})()})())
    assert acgw.mantle(method, "/v1/organization/projects/p/archive", idempotent=method != "POST", empty_ok=method == "POST") == want


@pytest.mark.parametrize("body", [b"", b"OK", b"[]"])
def test_an_empty_or_non_object_reply_is_an_error_for_a_listing_and_unclear_for_a_create(acgw, monkeypatch, body):
    # r85: the r84 shortcut read an empty listing page as "no projects" (a duplicate project per user) and gave a
    # create {} with no id.
    class Reply:
        status_code, content, text = 200, body, body.decode()

        def json(self):
            import json as _j
            return _j.loads(self.content)
    monkeypatch.setattr(acgw.requests, "request", lambda *a, **k: Reply())
    monkeypatch.setattr(acgw.S, "get_credentials", lambda: type("C", (), {"get_frozen_credentials": lambda self: type("F", (), {"access_key": "AKIDEXAMPLE", "secret_key": "x", "token": None})()})())
    with pytest.raises(acgw.MantleError, match="not a JSON object"):
        acgw.mantle("GET", "/v1/organization/projects")
    with pytest.raises(acgw.UnclearWrite, match="not a JSON object"):
        acgw.mantle("POST", "/v1/organization/projects", {"name": "x"}, idempotent=False)


def test_a_listing_page_with_more_but_no_last_id_is_an_error(acgw, monkeypatch):
    monkeypatch.setattr(acgw, "mantle", lambda *a, **k: {"data": [], "has_more": True})
    with pytest.raises(acgw.MantleError, match="no new last_id"):
        acgw._projects()


def test_new_content_with_a_stale_active_is_not_taken_as_success(acgw):
    # r85 (kept by design): the service can store an update before validating it and still report the old ACTIVE, so
    # without updatedAt the wait gives it 6 polls and sees the UPDATE_FAILED that follows.
    from agentcore_wait import wait_active
    reads = iter([{"status": "ACTIVE", "v": 2}] * 2 + [{"status": "UPDATE_FAILED", "v": 2}])
    with pytest.raises(RuntimeError, match="UPDATE_FAILED"):
        wait_active(lambda: next(reads), "x", before={"status": "ACTIVE", "v": 1}, sleep=lambda s: None)


def test_a_listing_that_is_not_json_is_an_error(acgw, monkeypatch):
    class Reply:
        status_code, content, text = 200, b"<html>", "<html>"

        def json(self):
            raise ValueError("not json")
    monkeypatch.setattr(acgw.requests, "request", lambda *a, **k: Reply())
    monkeypatch.setattr(acgw.S, "get_credentials", lambda: type("C", (), {"get_frozen_credentials": lambda self: type("F", (), {"access_key": "AKIDEXAMPLE", "secret_key": "x", "token": None})()})())
    with pytest.raises(acgw.MantleError, match="not a JSON object"):
        acgw.mantle("GET", "/v1/organization/projects")


def test_a_summary_with_an_empty_description_is_read_on_destroy(acgw, monkeypatch):
    # r84: description "" (not null) was neither matched nor read, so destroy left the limit behind.
    ctl = Pages([[{"rateLimitId": "a", "description": ""}]])
    real_get = ctl.get_gateway_rate_limit
    ctl.get_gateway_rate_limit = lambda **kw: {**real_get(**kw), "description": "d"}
    monkeypatch.setattr(acgw, "CTL", ctl)
    acgw.ratelimit_delete("gw", "d")
    assert ctl.deleted == ["a"]


def test_a_delete_that_answered_not_found_is_not_resent_on_a_stale_delete_failed(acgw):
    # r86: NotFound did not count as done, so a lagging DELETE_FAILED read resent the delete every 3 polls until the
    # wait timed out. The service said it is gone: nothing is waited for.
    import botocore.exceptions
    from agentcore_wait import delete_and_wait
    deletes, reads = [], iter([{"status": "DELETE_FAILED"}] * 4 + [None])

    def delete(i):
        deletes.append(i)
        raise botocore.exceptions.ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "Delete")
    import agentcore_wait
    real = agentcore_wait.time.sleep
    agentcore_wait.time.sleep = lambda s: None
    try:
        assert delete_and_wait({"a": {"status": "DELETE_FAILED"}}, delete, lambda i: next(reads)) == []
    finally:
        agentcore_wait.time.sleep = real
    assert deletes == ["a"]


def test_a_refused_delete_is_retried_past_a_stale_active_read(acgw):
    # r87: the one resend after a ConflictException came straight after a read that still showed the old ACTIVE, so
    # it was refused again and stopped the step, although the update in progress would have finished.
    import botocore.exceptions
    from agentcore_wait import delete_and_wait
    tries, sleeps = [], []
    state = {"gone": False}

    def delete(i):
        tries.append(i)
        if len(tries) < 3:
            raise botocore.exceptions.ClientError({"Error": {"Code": "ConflictException"}}, "Delete")
        state["gone"] = True
    import agentcore_wait
    real = agentcore_wait.time.sleep
    agentcore_wait.time.sleep = sleeps.append
    try:
        assert delete_and_wait({"a": {"status": "ACTIVE"}}, delete, lambda i: None if state["gone"] else {"status": "ACTIVE"}) == ["a"]
    finally:
        agentcore_wait.time.sleep = real
    assert len(tries) == 3 and sleeps == [5] * 12  # no change visible: the 6-poll stale window per resend


def test_a_refused_delete_that_finds_another_runs_delete_failing_is_sent_again(acgw):
    # r87: the refused entry counted as tried, so when the other run's delete then failed there was no retry.
    import botocore.exceptions
    import agentcore_wait
    tries = []
    reads = iter([{"status": "DELETING"}, {"status": "DELETE_FAILED"}])
    state = {"gone": False}

    def delete(i):
        tries.append(i)
        if len(tries) == 1:
            raise botocore.exceptions.ClientError({"Error": {"Code": "ConflictException"}}, "Delete")
        state["gone"] = True

    def get(i):
        return None if state["gone"] else next(reads, {"status": "DELETE_FAILED"})
    real = agentcore_wait.time.sleep
    agentcore_wait.time.sleep = lambda s: None
    try:
        agentcore_wait.delete_and_wait({"a": {"status": "ACTIVE"}}, delete, get)
    finally:
        agentcore_wait.time.sleep = real
    assert len(tries) == 2


def test_a_rate_limit_create_reply_without_an_id_stops_with_a_message(acgw, monkeypatch):
    ctl = _OneLimit(None)
    ctl.create_gateway_rate_limit = lambda **kw: {}
    monkeypatch.setattr(acgw, "CTL", ctl)
    with pytest.raises(SystemExit, match="the rate limit could not be created: the reply carries no id"):
        acgw.ratelimit_ensure("gw", json.dumps(_spec()))


def test_delete_and_wait_takes_sleep_for_every_wait(acgw):
    # r89: sleep= was dropped from delete_and_wait only; it now reaches the settle and the gone wait too.
    from agentcore_wait import delete_and_wait
    sleeps, reads = [], iter([{"status": "UPDATING"}, {"status": "ACTIVE"}, {"status": "DELETING"}, None])
    delete_and_wait({"a": {"status": "UPDATING"}}, lambda i: None, lambda i: next(reads), sleep=sleeps.append)
    assert sleeps == [5, 5]  # one settle poll, one gone poll, all through the injected sleep


def test_ensure_removes_this_stacks_limit_on_other_keys(acgw, monkeypatch):
    # r178 live: switching idp (Cognito username -> sub) created the sub limit, but Terraform ran no destroy step for
    # the deposed instance, so the username limit stayed. ensure deletes it once the new one is up; another stack's
    # limit (another description base) is left alone.
    import json
    sub, user = "$.context.jwt.sub", "$.context.jwt.username"

    class Ctl(Pages):
        def create_gateway_rate_limit(self, **kw):
            self.created.append(kw["description"])
            self.pages[0].append({"rateLimitId": "new", "description": kw["description"], "dimensionKeys": kw["dimensionKeys"], "entries": kw["entries"]})
            return {"rateLimitId": "new"}

    old = {"rateLimitId": "old", "description": "ocgw per user #aaa ~111", "dimensionKeys": [user], "entries": _spec()["entries"]}
    other = {"rateLimitId": "theirs", "description": "someone else #x", "dimensionKeys": [user], "entries": []}
    ctl = Ctl([[old, other]])
    monkeypatch.setattr(acgw, "CTL", ctl)
    spec = {"description": "ocgw per user #bbb", "dimensionKeys": [sub], "entries": [{"dimensions": {sub: "*"}, "requests": [{"rate": 60, "period": "minute"}]}]}
    acgw.ratelimit_ensure("gw", json.dumps(spec))
    assert ctl.deleted == ["old"] and {r["rateLimitId"] for r in ctl.pages[0]} == {"new", "theirs"}


def test_python_sh_pins_match():
    # r180 content scan: the uv fallback took any boto3 >= the minimum, so a new release was picked up unseen.
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[2]
    sh = (root / "terraform" / "helpers" / "python.sh").read_text()
    req = dict(re.findall(r"^([a-z0-9_-]+)(?:\[[^\]]*\])?==(\S+)$", (root / "tests" / "requirements.txt").read_text(), re.M))
    assert dict(re.findall(r'--with "([a-z0-9_-]+)==([^"]+)"', sh)) == {k: req[k] for k in ("boto3", "requests")}
    import importlib.util
    spec = importlib.util.spec_from_file_location("agentcore_wait", root / "terraform" / "helpers" / "agentcore_wait.py")
    w = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(w)
    assert tuple(int(x) for x in req["boto3"].split(".")) >= w.MIN_BOTO3  # the pin meets the floor the script enforces
    # r196 threat model T-039: the pins' own dependencies resolve as of a fixed date and only from wheels, and the
    # date is no older than the latest unit run recorded in docs/test-results.md (the versions the suite ran with).
    import datetime
    uv = next(line for line in sh.splitlines() if "uv run" in line and "exec" in line)
    assert "--no-build" in uv.split()
    when = re.search(r'--exclude-newer "(\d{4}-\d{2}-\d{2})T00:00:00Z"', uv)
    assert when, uv
    rows = re.findall(r"^\| (\d{1,2} [A-Z][a-z]{2} \d{4}) \| r\d+", (root / "docs" / "test-results.md").read_text(), re.M)
    latest = max(datetime.datetime.strptime(d, "%d %b %Y").date() for d in rows)
    assert datetime.date.fromisoformat(when.group(1)) >= latest, (when.group(1), latest)
