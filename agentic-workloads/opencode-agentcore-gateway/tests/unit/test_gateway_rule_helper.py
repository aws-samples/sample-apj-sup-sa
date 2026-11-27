# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""terraform/helpers/gateway_rule.py: apply updates in place, skips DELETING entries, and the create_before_destroy
destroy step leaves a rule the replacement already updated."""
import json
import runpy
import sys
from pathlib import Path

import boto3
import pytest

SRC = str(Path(__file__).resolve().parents[2] / "terraform" / "helpers" / "gateway_rule.py")
SPEC = {"region": "us-east-1", "gateway": "gw1", "description": "ocgw fallback a", "priority": 1, "match": ["m1"],
        "target": "ow", "model": "m1", "fallback": [{"target": "ow", "model": "m2"}], "status_codes": [429],
        "errors": [], "first_byte_timeout_ms": 1000}


class _NotFound(Exception):
    """As botocore's modelled ResourceNotFoundException: the error code is on .response."""
    response = {"Error": {"Code": "ResourceNotFoundException"}}


class _Exc:
    ResourceNotFoundException = _NotFound


class FakeCp:
    exceptions = _Exc

    def __init__(self, rules):
        self.rules = {r["ruleId"]: dict(r) for r in rules}
        self.calls = []

    def list_gateway_rules(self, gatewayIdentifier, nextToken=None):  # noqa: N803
        return {"gatewayRules": [dict(r) for r in self.rules.values()]}

    def get_gateway_rule(self, gatewayIdentifier, ruleId):  # noqa: N803
        if ruleId not in self.rules:  # as the service: a deleted rule is a ResourceNotFoundException
            raise _NotFound(ruleId)
        return dict(self.rules[ruleId])

    def create_gateway_rule(self, gatewayIdentifier, **kw):  # noqa: N803
        self.calls.append(("create",))
        self.rules["new"] = {"gatewayRuleId": "new", "ruleId": "new", "status": "ACTIVE", **kw}
        return {"gatewayRuleId": "new"}  # the id under the other key rid() accepts

    def update_gateway_rule(self, gatewayIdentifier, ruleId, **kw):  # noqa: N803
        self.calls.append(("update", ruleId))
        self.rules[ruleId].update(kw, status="ACTIVE")

    def delete_gateway_rule(self, gatewayIdentifier, ruleId):  # noqa: N803
        self.calls.append(("delete", ruleId))
        self.rules.pop(ruleId)


def run(monkeypatch, cp, mode, spec=SPEC):
    monkeypatch.setenv("RULE_SPEC", json.dumps(spec))
    monkeypatch.setattr(boto3, "Session", lambda **kw: type("S", (), {"client": lambda self, name: cp})())
    monkeypatch.setattr(sys, "argv", ["gateway_rule.py", mode])
    monkeypatch.setattr("time.sleep", lambda s: None)
    return runpy.run_path(SRC, run_name="__main__")


def gone_after(cp, rule_id, reads):
    """Make an out-of-band or in-flight delete of rule_id finish after `reads` GetGatewayRule calls for it."""
    real_get, n = cp.get_gateway_rule, {"n": 0}

    def get(**kw):
        if kw["ruleId"] == rule_id:
            n["n"] += 1
            if n["n"] > reads:
                cp.rules.pop(rule_id, None)
        return real_get(**kw)
    cp.get_gateway_rule = get


def rule(rid, status="ACTIVE", **kw):
    return {"ruleId": rid, "description": SPEC["description"], "status": status, "priority": 1, **kw}


def test_create_uses_rid_for_either_id_key(monkeypatch):
    cp = FakeCp([])
    run(monkeypatch, cp, "apply")
    assert cp.calls == [("create",)]


def test_deleting_entry_is_not_updated_and_create_waits(monkeypatch):
    cp = FakeCp([rule("old", status="DELETING")])
    gone_after(cp, "old", 2)  # the in-flight delete finishes
    run(monkeypatch, cp, "apply")
    assert ("update", "old") not in cp.calls and cp.calls[-1] == ("create",)


def test_changed_spec_updates_in_place_then_old_destroy_keeps_it(monkeypatch):
    cp = FakeCp([])
    run(monkeypatch, cp, "apply")  # old spec deployed
    new = {**SPEC, "fallback": [{"target": "ow", "model": "m3"}]}
    run(monkeypatch, cp, "apply", new)  # replacement instance (create_before_destroy) updates in place
    assert ("update", "new") in cp.calls
    run(monkeypatch, cp, "delete", SPEC)  # old instance destroyed with its old spec
    assert "new" in cp.rules and not any(c[0] == "delete" for c in cp.calls)


def test_destroy_deletes_rule_matching_its_spec(monkeypatch):
    cp = FakeCp([])
    run(monkeypatch, cp, "apply")
    run(monkeypatch, cp, "delete")
    assert cp.rules == {}


@pytest.mark.parametrize("mode", ["bogus"])
def test_bad_mode(monkeypatch, mode):
    with pytest.raises(SystemExit):
        run(monkeypatch, FakeCp([]), mode)


def test_echoed_extra_fields_still_count_as_unchanged(monkeypatch):
    cp = FakeCp([])
    run(monkeypatch, cp, "apply")
    act = cp.rules["new"]["actions"][0]["routeToTarget"]["staticRoute"]["fallback"]
    act["maxAttempts"] = 3  # a defaulted field the service echoes back
    cp.rules["new"]["createdAt"] = "now"
    run(monkeypatch, cp, "apply")
    assert not any(c[0] == "update" for c in cp.calls)
    run(monkeypatch, cp, "delete")  # the destroy step still recognises it as its own rule
    assert cp.rules == {}


def test_replacement_with_unchanged_body_keeps_the_rule(monkeypatch):
    cp = FakeCp([])
    run(monkeypatch, cp, "apply")
    new = {**SPEC, "match": SPEC["match"] + []}  # same rule body ...
    new["note"] = "spec changed, body did not"   # ... but a different spec, so Terraform replaced the resource
    run(monkeypatch, cp, "apply", new)
    run(monkeypatch, cp, "delete", SPEC)  # old instance's destroy
    assert len(cp.rules) == 1 and not any(c[0] == "delete" for c in cp.calls)


def test_destroy_removes_a_rule_made_before_hashed_descriptions(monkeypatch):
    cp = FakeCp([rule("legacy")])  # bare description, as created by an older helper
    run(monkeypatch, cp, "delete")
    assert cp.rules == {}


def test_update_failed_rule_is_updated_in_place_not_deleted(monkeypatch):
    cp = FakeCp([])
    run(monkeypatch, cp, "apply")  # a rule from SPEC, then the service marks a later update as failed
    cp.rules["new"]["status"] = "UPDATE_FAILED"
    cp.calls.clear()
    run(monkeypatch, cp, "apply")
    assert cp.calls == [("update", "new")]  # still serving its last config: never deleted first


def test_create_failed_entry_is_removed_and_recreated(monkeypatch):
    cp = FakeCp([rule("bad", status="CREATE_FAILED")])
    run(monkeypatch, cp, "apply")
    assert cp.calls[0] == ("delete", "bad") and cp.calls[-1] == ("create",)


def test_a_failed_duplicate_goes_but_the_healthy_rule_is_kept(monkeypatch):
    cp = FakeCp([])
    run(monkeypatch, cp, "apply")
    cp.rules["dup"] = rule("dup", status="CREATE_FAILED")
    cp.calls.clear()
    run(monkeypatch, cp, "apply")
    assert ("delete", "dup") in cp.calls and ("delete", "new") not in cp.calls and "new" in cp.rules


def test_with_a_failed_and_a_healthy_copy_the_healthy_one_is_kept(monkeypatch):
    cp = FakeCp([])
    run(monkeypatch, cp, "apply")
    cp.rules = {"bad": {**cp.rules["new"], "ruleId": "bad", "status": "UPDATE_FAILED"}, "new": cp.rules["new"]}  # failed listed first
    cp.calls.clear()
    run(monkeypatch, cp, "apply")
    # r84: the kept copy already matches the spec, so it is not updated for nothing.
    assert ("delete", "bad") in cp.calls and ("delete", "new") not in cp.calls and ("update", "new") not in cp.calls


def test_a_duplicate_of_a_changed_rule_is_deleted_and_the_kept_one_updated(monkeypatch):
    cp = FakeCp([rule("a", description="ocgw fallback a #oldhash1"), rule("b", description="ocgw fallback a #oldhash1")])
    run(monkeypatch, cp, "apply")
    assert len([c for c in cp.calls if c[0] == "delete"]) == 1 and len([c for c in cp.calls if c[0] == "update"]) == 1


def test_a_delete_that_fails_again_stops_with_its_reason(monkeypatch):
    cp = FakeCp([rule("stuck", status="DELETE_FAILED", statusReasons=["in use"])])
    cp.delete_gateway_rule = lambda gatewayIdentifier, ruleId: cp.calls.append(("delete", ruleId))  # stays DELETE_FAILED
    with pytest.raises(SystemExit, match="in use"):
        run(monkeypatch, cp, "apply")


def test_a_stale_update_failed_right_after_the_update_is_waited_out(monkeypatch):
    cp = FakeCp([])
    run(monkeypatch, cp, "apply")
    cp.rules["new"]["status"] = "UPDATE_FAILED"
    polls = {"n": 0}
    real_get, real_update = cp.get_gateway_rule, cp.update_gateway_rule

    def update(gatewayIdentifier, ruleId, **kw):  # noqa: N803
        real_update(gatewayIdentifier, ruleId, **kw)
        cp.rules[ruleId]["status"] = "UPDATE_FAILED"  # the service still shows the old status for a moment

    def get(gatewayIdentifier, ruleId):  # noqa: N803
        r = real_get(gatewayIdentifier, ruleId)
        if cp.calls and cp.calls[-1][0] == "update":
            polls["n"] += 1
            if polls["n"] > 2:
                cp.rules[ruleId]["status"] = "ACTIVE"
        return r
    cp.update_gateway_rule, cp.get_gateway_rule = update, get
    run(monkeypatch, cp, "apply")  # no SystemExit: the stale status is within the grace polls


def test_an_update_rejected_after_a_stale_active_is_reported(monkeypatch):
    # r47: straight after UpdateGatewayRule the old ACTIVE can still be read; the helper must not take it as success.
    cp = FakeCp([])
    run(monkeypatch, cp, "apply")
    polls = {"n": 0}
    real_get = cp.get_gateway_rule

    def get(gatewayIdentifier, ruleId):  # noqa: N803
        if cp.calls and cp.calls[-1][0] == "update":
            polls["n"] += 1
            if polls["n"] > 2:  # two stale ACTIVE reads, then the service rejects the change
                cp.rules[ruleId].update(status="UPDATE_FAILED", statusReasons=["bad hop"])
        return real_get(gatewayIdentifier, ruleId)
    cp.get_gateway_rule = get
    with pytest.raises(SystemExit, match="bad hop"):
        run(monkeypatch, cp, "apply", {**SPEC, "first_byte_timeout_ms": 2000})


def test_an_update_shown_by_updated_at_returns_at_once(monkeypatch):
    cp = FakeCp([])
    run(monkeypatch, cp, "apply")
    cp.rules["new"]["updatedAt"] = "t0"
    real_update = cp.update_gateway_rule

    def update(gatewayIdentifier, ruleId, **kw):  # noqa: N803
        real_update(gatewayIdentifier, ruleId, **kw)
        cp.rules[ruleId]["updatedAt"] = "t1"
    cp.update_gateway_rule = update
    gets = {"n": 0}
    real_get = cp.get_gateway_rule
    cp.get_gateway_rule = lambda gatewayIdentifier, ruleId: (gets.__setitem__("n", gets["n"] + 1), real_get(gatewayIdentifier, ruleId))[1]  # noqa: N803
    run(monkeypatch, cp, "apply", {**SPEC, "first_byte_timeout_ms": 2000})
    assert gets["n"] == 2  # the read of the entry before the update, then one wait poll


def test_a_stale_failed_status_after_re_deleting_a_failed_entry_is_waited_out(monkeypatch):
    # r55: an entry already DELETE_FAILED, deleted again, can still read DELETE_FAILED for a poll or two.
    cp = FakeCp([rule("stuck", status="DELETE_FAILED")])
    polls = {"n": 0}

    def delete(gatewayIdentifier, ruleId):  # noqa: N803
        cp.calls.append(("delete", ruleId))
    real_get = cp.get_gateway_rule

    def get(**kw):  # still DELETE_FAILED (the old status) for two reads after the delete, then gone
        if any(c[0] == "delete" for c in cp.calls):
            polls["n"] += 1
            if polls["n"] > 2:
                cp.rules.pop("stuck", None)  # the second delete goes through
        return real_get(**kw)
    cp.delete_gateway_rule, cp.get_gateway_rule = delete, get
    run(monkeypatch, cp, "apply")  # no SystemExit
    assert cp.calls[0] == ("delete", "stuck") and cp.calls[-1] == ("create",)


def test_a_rule_updating_that_then_fails_is_updated_not_an_exit(monkeypatch):
    # r77: the in-flight wait raised on UPDATE_FAILED, so the update that repairs the rule never ran.
    cp = FakeCp([rule("r1", status="UPDATING")])
    seq = iter(["UPDATING", "UPDATE_FAILED"])
    real_get = cp.get_gateway_rule
    cp.get_gateway_rule = lambda **kw: {**real_get(**kw), "status": next(seq, cp.rules[kw["ruleId"]]["status"])}
    run(monkeypatch, cp, "apply")
    assert ("update", "r1") in cp.calls and cp.rules["r1"]["status"] == "ACTIVE"


def test_duplicates_are_deleted_and_waited_out_before_the_update(monkeypatch):
    # r77: a bare delete left the duplicate DELETING, and the update of the kept rule was refused as a duplicate.
    cp = FakeCp([rule("a"), rule("b")])
    run(monkeypatch, cp, "apply")
    deletes = [c for c in cp.calls if c[0] == "delete"]
    assert len(deletes) == 1 and cp.calls.index(deletes[0]) < cp.calls.index(("update", "a" if deletes[0][1] == "b" else "b"))
    assert len(cp.rules) == 1


def test_destroy_with_the_gateway_already_gone_succeeds(monkeypatch, capsys):
    # r77: list_gateway_rules raised ResourceNotFoundException as a traceback on every fallback rule.
    cp = FakeCp([])
    cp.exceptions = _Exc

    def gone(**kw):
        raise _NotFound("gateway")
    cp.list_gateway_rules = gone
    run(monkeypatch, cp, "delete")
    assert "gateway gone" in capsys.readouterr().out


def test_a_rule_being_deleted_is_waited_out_and_created_again_not_kept(monkeypatch):
    # r78: a DELETING entry stayed in `found`, was kept and updated (refused) or reported unchanged (wait raised).
    cp = FakeCp([rule("old", status="UPDATING")])  # listed UPDATING, so the live listing keeps it
    gone_after(cp, "old", 3)  # the out-of-band delete finishes
    real_get = cp.get_gateway_rule

    def get(**kw):  # the first read finds it DELETING (an out-of-band delete began); the list then shows it too
        if kw["ruleId"] == "old" and "old" in cp.rules:
            cp.rules["old"]["status"] = "DELETING"
        return real_get(**kw)
    cp.get_gateway_rule = get
    run(monkeypatch, cp, "apply")
    assert ("update", "old") not in cp.calls and cp.calls[-1] == ("create",)


def test_destroy_waits_out_an_in_flight_rule_before_deleting(monkeypatch):
    # r79: the destroy sent DeleteGatewayRule for an UPDATING entry, which the service refuses.
    cp = FakeCp([rule("r1", status="UPDATING")])
    real_get, real_delete = cp.get_gateway_rule, cp.delete_gateway_rule
    reads = {"n": 0}

    def get(**kw):
        reads["n"] += 1
        if reads["n"] >= 2 and "r1" in cp.rules:
            cp.rules["r1"]["status"] = "ACTIVE"
        return real_get(**kw)

    def delete(**kw):
        assert cp.rules[kw["ruleId"]]["status"] == "ACTIVE", "deleted while still UPDATING"
        real_delete(**kw)
    cp.get_gateway_rule, cp.delete_gateway_rule = get, delete
    run(monkeypatch, cp, "delete")
    assert ("delete", "r1") in cp.calls and "r1" not in cp.rules


class _Conflict(Exception):
    response = {"Error": {"Code": "ConflictException"}}


def test_teardown_goes_by_the_rules_own_status_not_the_lagging_list(monkeypatch):
    # r80: the list still showed ACTIVE while the rule was UPDATING; the delete was sent at once and refused.
    cp = FakeCp([rule("r1")])
    real_list, real_get, real_delete = cp.list_gateway_rules, cp.get_gateway_rule, cp.delete_gateway_rule
    cp.list_gateway_rules = lambda **kw: {"gatewayRules": [{**r, "status": "ACTIVE"} for r in real_list(**kw)["gatewayRules"]]}
    cp.rules["r1"]["status"] = "UPDATING"
    reads = {"n": 0}

    def get(**kw):
        reads["n"] += 1
        if reads["n"] >= 3 and "r1" in cp.rules:
            cp.rules["r1"]["status"] = "ACTIVE"
        return real_get(**kw)

    def delete(**kw):
        assert cp.rules[kw["ruleId"]]["status"] == "ACTIVE", "deleted while still UPDATING"
        real_delete(**kw)
    cp.get_gateway_rule, cp.delete_gateway_rule = get, delete
    run(monkeypatch, cp, "delete")
    assert ("delete", "r1") in cp.calls and "r1" not in cp.rules


def test_a_rule_renamed_by_the_replacement_while_it_settled_is_left_alone(monkeypatch, capsys):
    # r80: the old instance's destroy deleted whatever the settle returned, even a rule its replacement had taken over.
    cp = FakeCp([rule("r1", status="UPDATING", description=SPEC["description"])])
    real_get = cp.get_gateway_rule
    reads = {"n": 0}

    def get(**kw):
        reads["n"] += 1
        if reads["n"] >= 2:
            cp.rules["r1"].update(status="ACTIVE", description=SPEC["description"] + " #newhash1 ~newowner")
        return real_get(**kw)
    cp.get_gateway_rule = get
    run(monkeypatch, cp, "delete")
    assert "r1" in cp.rules and not any(c[0] == "delete" for c in cp.calls)
    assert "left alone" in capsys.readouterr().out


def test_a_delete_refused_for_a_change_started_since_the_read_settles_and_retries(monkeypatch):
    # r80: a ConflictException from DeleteGatewayRule ended the destroy with a traceback.
    cp = FakeCp([rule("r1")])
    real_delete, tries = cp.delete_gateway_rule, {"n": 0}

    def delete(**kw):
        tries["n"] += 1
        if tries["n"] == 1:
            raise _Conflict("a change is in progress")
        real_delete(**kw)
    cp.delete_gateway_rule = delete
    run(monkeypatch, cp, "delete")
    assert tries["n"] == 2 and "r1" not in cp.rules


def test_a_delete_refused_twice_stops_with_a_message(monkeypatch):
    cp = FakeCp([rule("r1")])

    def delete(**kw):
        raise _Conflict("a change is in progress")
    cp.delete_gateway_rule = delete
    with pytest.raises(SystemExit, match="rule r1 could not be deleted: a change is in progress"):
        run(monkeypatch, cp, "delete")


def test_destroy_goes_by_the_get_description_not_the_lagging_list(monkeypatch, capsys):
    # r81: the list still showed this instance's description while the Get showed the replacement's; the ACTIVE rule
    # was deleted on the list's word, removing the replacement's fallback rule.
    cp = FakeCp([rule("r1", description=SPEC["description"] + " #newhash1 ~newowner")])
    real_list = cp.list_gateway_rules
    cp.list_gateway_rules = lambda **kw: {"gatewayRules": [{**r, "description": SPEC["description"]} for r in real_list(**kw)["gatewayRules"]]}
    run(monkeypatch, cp, "delete")
    assert "r1" in cp.rules and not any(c[0] == "delete" for c in cp.calls)


def test_destroy_deletes_its_own_rule_even_when_the_list_still_shows_another_description(monkeypatch):
    cp = FakeCp([rule("r1")])  # Get: this instance's (bare) description
    real_list = cp.list_gateway_rules
    cp.list_gateway_rules = lambda **kw: {"gatewayRules": [{**r, "description": SPEC["description"] + " #oldhash1"} for r in real_list(**kw)["gatewayRules"]]}
    run(monkeypatch, cp, "delete")
    assert "r1" not in cp.rules


def test_apply_repairs_an_entry_the_lagging_list_still_shows_deleting(monkeypatch):
    # r81: the live listing dropped entries the list called DELETING before any Get, so a DELETE_FAILED one was never repaired.
    cp = FakeCp([rule("a"), rule("dup", status="DELETE_FAILED")])
    real_list = cp.list_gateway_rules
    cp.list_gateway_rules = lambda **kw: {"gatewayRules": [{**r, "status": "DELETING"} if r["ruleId"] == "dup" else r for r in real_list(**kw)["gatewayRules"]]}
    run(monkeypatch, cp, "apply")
    assert ("delete", "dup") in cp.calls and "dup" not in cp.rules


def test_apply_lists_the_rules_once(monkeypatch):
    # r81: each delete_all re-listed every page and re-read every entry already read.
    cp = FakeCp([rule("a"), rule("b"), rule("c", status="CREATE_FAILED")])
    real_list, lists = cp.list_gateway_rules, []
    cp.list_gateway_rules = lambda **kw: lists.append(1) or real_list(**kw)
    run(monkeypatch, cp, "apply")
    assert len(lists) == 1 and len(cp.rules) == 1


def test_an_update_refused_for_a_change_in_progress_stops_with_a_rerun_message(monkeypatch):
    # r82: UpdateGatewayRule's ConflictException ended the apply with a traceback. r83: no resend (it would be judged
    # against a read from before the conflicting change): a message asks for the run to be repeated.
    cp = FakeCp([rule("r1", description="ocgw fallback a #oldhash1")])
    real_update, tries = cp.update_gateway_rule, []

    def update(**kw):
        tries.append(1)
        if len(tries) == 1:
            raise _Conflict("a change is in progress")
        real_update(**kw)
    cp.update_gateway_rule = update
    with pytest.raises(SystemExit, match="rule r1 could not be updated: a change is in progress.*run terraform again"):
        run(monkeypatch, cp, "apply")
    assert len(tries) == 1


def test_a_create_refused_stops_with_a_message(monkeypatch):
    cp = FakeCp([])

    def create(**kw):
        raise _Conflict("a rule with this description exists")
    cp.create_gateway_rule = create
    with pytest.raises(SystemExit, match="the rule could not be created: a rule with this description exists"):
        run(monkeypatch, cp, "apply")


def test_an_out_of_band_delete_that_fails_is_deleted_again(monkeypatch):
    # r84: an entry read DELETING that then ended DELETE_FAILED failed the apply on the first read instead of being
    # deleted again and the rule created.
    cp = FakeCp([rule("old", status="DELETING")])
    real_get, real_delete = cp.get_gateway_rule, cp.delete_gateway_rule
    reads = {"n": 0}

    def get(**kw):
        if kw["ruleId"] == "old" and "old" in cp.rules:
            reads["n"] += 1
            if reads["n"] == 2:
                cp.rules["old"]["status"] = "DELETE_FAILED"  # the out-of-band delete failed
        return real_get(**kw)
    cp.get_gateway_rule = get
    run(monkeypatch, cp, "apply")
    assert ("delete", "old") in cp.calls and cp.calls[-1] == ("create",)


def test_apply_waits_for_deleting_dead_and_duplicate_entries_in_one_loop(monkeypatch):
    # r84: three delete_all passes, each with its own wait loop.
    cp = FakeCp([rule("a"), rule("dup"), rule("dead", status="CREATE_FAILED"), rule("going", status="DELETING")])
    gone_after(cp, "going", 2)
    sys.path.insert(0, str(Path(SRC).parent))
    import agentcore_wait
    passes, real = [], agentcore_wait.delete_and_wait
    monkeypatch.setattr(agentcore_wait, "delete_and_wait", lambda *a, **k: passes.append(sorted(a[0])) or real(*a, **k))
    run(monkeypatch, cp, "apply")
    assert len(passes) == 1 and len(passes[0]) == 3 and len(cp.rules) == 1


def test_of_two_active_duplicates_the_one_matching_the_spec_is_kept(monkeypatch):
    # r86: the keep was chosen by status alone, so the matching copy could be deleted and the other rewritten.
    cp = FakeCp([])
    run(monkeypatch, cp, "apply")  # "new" carries the spec
    cp.rules = {"old": {**cp.rules["new"], "ruleId": "old", "description": "ocgw fallback a #oldhash1"}, "new": cp.rules["new"]}
    cp.calls.clear()
    run(monkeypatch, cp, "apply")
    assert cp.calls == [("delete", "old")]


def test_a_rule_create_reply_without_an_id_stops_with_a_message(monkeypatch):
    cp = FakeCp([])
    cp.create_gateway_rule = lambda **kw: {}
    with pytest.raises(SystemExit, match="the rule could not be created: the reply carries no id"):
        run(monkeypatch, cp, "apply")
