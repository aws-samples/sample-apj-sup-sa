# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Terraform custom resource for an AgentCore Gateway rule with model matching and fallback (fallback.tf).

  RULE_SPEC='<json>' python helpers/gateway_rule.py apply     # create, or update in place when the spec changed; waits for ACTIVE
  RULE_SPEC='<json>' python helpers/gateway_rule.py delete    # delete if the rule still matches this spec (terraform destroy)

The AWS provider's aws_bedrockagentcore_gateway_rule has no matchModels, model or fallback fields yet (checked on
provider 6.69.1, 23 Oct 2026), so terraform_data runs this script. Needs boto3 >= 1.43.118.
RULE_SPEC: {"region", "profile", "gateway", "description", "priority", "match": [...], "target", "model",
            "fallback": [{"target", "model"}], "status_codes": [...], "errors": [...], "first_byte_timeout_ms"}
The rule is found by its description, so the description must be unique on the gateway. The stored description is
"<description> #<hash of the spec>": each Terraform instance owns exactly one description, so the destroy step of an
instance replaced with create_before_destroy can tell its own rule from the one its replacement renamed.
"""
import hashlib
import json
import os
import sys

import boto3
import botocore.exceptions

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agentcore_wait  # noqa: E402

agentcore_wait.require_boto3(boto3.__version__)
agentcore_wait.drop_empty_profile()

if len(sys.argv) != 2 or sys.argv[1] not in ("apply", "delete"):
    raise SystemExit("usage: RULE_SPEC='<json>' gateway_rule.py apply|delete")
# Every key the command reads, checked up front: a spec fallback.tf no longer builds in full is a message, not a
# KeyError traceback halfway through.
NEEDS = {"delete": ("region", "gateway", "description"),
         "apply": ("region", "gateway", "description", "priority", "match", "target", "model", "status_codes",
                   "errors", "first_byte_timeout_ms", "fallback")}
try:
    spec = json.loads(os.environ["RULE_SPEC"])
    if not isinstance(spec, dict):
        raise TypeError("RULE_SPEC is not a JSON object")
    missing = [k for k in NEEDS[sys.argv[1]] if k not in spec]
    if sys.argv[1] == "apply" and not missing:
        missing = [f"{k} as a list" for k in ("match", "status_codes", "errors", "fallback")
                   if not isinstance(spec[k], list)]  # a string would be iterated character by character
        if not missing and not all(isinstance(f, dict) and "target" in f and "model" in f for f in spec["fallback"]):
            missing = ["a target and model in every fallback entry"]
except (KeyError, ValueError, TypeError) as e:
    raise SystemExit(f"gateway_rule.py: RULE_SPEC missing or malformed ({type(e).__name__}: {e})") from None
if missing:
    raise SystemExit(f"gateway_rule.py {sys.argv[1]}: RULE_SPEC lacks {', '.join(missing)}")
# AWS_PROFILE is passed explicitly, as acgw.py does: botocore reads AWS_DEFAULT_PROFILE first, so a shell that sets
# both (some sandboxes export AWS_DEFAULT_PROFILE=default) would otherwise sign with the wrong profile.
try:  # a profile that does not exist fails here: a message, not a traceback
    cp = boto3.Session(profile_name=spec.get("profile") or os.environ.get("AWS_PROFILE") or None,
                       region_name=spec["region"]).client("bedrock-agentcore-control")
except botocore.exceptions.BotoCoreError as e:
    raise SystemExit(f"gateway_rule.py: AWS session not set up: {e}") from None
GW = spec["gateway"]
BASE = spec["description"]
HASHED = f"{BASE} #" + hashlib.sha256(json.dumps({k: v for k, v in spec.items() if k not in ("profile", "region")},
                                                  sort_keys=True).encode()).hexdigest()[:8]
# This Terraform instance's tag (its terraform_data id): a replacement with an unchanged spec (terraform apply -replace)
# retags the rule, so the old instance's destroy step, which deletes only a rule carrying its own tag, leaves it alone.
OWNER = os.environ.get("RULE_OWNER", "")[:8]
DESC = f"{HASHED} ~{OWNER}" if OWNER else HASHED


def body():
    return dict(
        priority=spec["priority"],
        description=DESC,
        conditions=[{"matchModels": {"anyOf": [{"value": m} for m in spec["match"]]}}],
        actions=[{"routeToTarget": {"staticRoute": {
            "targetName": spec["target"], "model": spec["model"],
            "fallback": {
                "circuitBreaker": {"mode": "ENABLED"},
                "triggerOn": {"statusCodes": spec["status_codes"], "errors": spec["errors"]},
                "firstByteTimeoutMilliseconds": spec["first_byte_timeout_ms"],
                "targetFallback": [{"targetName": f["target"], "model": f["model"]} for f in spec["fallback"]],
            }}}}],
    )


def match(d, own=False):
    """True when description d is this rule's: any version of it (BASE, or BASE #<hash>), or with own only this
    instance's. own also takes the bare BASE (a rule made before descriptions carried a hash is still this instance's
    to delete: a replacement would already have renamed it to its own hash) and HASHED without a tag (a rule an older
    helper set before descriptions carried one)."""
    d = d or ""
    return d in (DESC, BASE, HASHED) if own else (d == BASE or d.startswith(f"{BASE} #"))


def mine():
    """This rule's entries across every page of ListGatewayRules: any version of it (match), DELETING ones too. The
    list can lag, so only GetGatewayRule decides status and ownership."""
    return [x for x in agentcore_wait.list_all(cp.list_gateway_rules, ("gatewayRules",), gatewayIdentifier=GW)
            if match(x.get("description"))]


def rid(r):
    return r.get("ruleId") or r.get("gatewayRuleId")


def covers(got, want):
    """True when got holds every field of want with the same value. The service may echo extra or defaulted fields
    (for example a fallback attribute we never set); those must not make an unchanged rule look changed."""
    if isinstance(want, dict):
        return isinstance(got, dict) and all(covers(got.get(k), v) for k, v in want.items())
    if isinstance(want, list):
        return isinstance(got, list) and len(got) == len(want) and all(covers(g, w) for g, w in zip(got, want))
    return got == want


def same(full):
    """full: the rule as GetGatewayRule returns it."""
    want = body()
    return full.get("description") == DESC and all(covers(full.get(k), want[k]) for k in ("priority", "conditions", "actions"))


def reason(full):
    return full.get("statusReasons") or full.get("statusReason") or "no reason given"


def owned(f, own):
    """f (a rule in full), or None when it is gone or, on destroy (own), carries another instance's description."""
    if f is not None and own and not match(f.get("description"), own=True):
        print("left alone (another instance's now)", rid(f))
        return None
    return f


def read(own=False):
    """{rule id: the rule in full} for every listed entry this instance may act on, read by Get."""
    return {i: f for i, f in get_rules(rid(r) for r in mine()) if owned(f, own) is not None}


def delete_all(entries, own=False):
    """Delete these entries ({rule id: the rule as read by Get}) and wait until they are gone, so a create that follows
    is not rejected for a rule still DELETING with the same description. own (the destroy step): Terraform replaces
    the resource with create_before_destroy, so by the time the old instance is destroyed the new one has already
    renamed the rule to its own description (even when the body is unchanged); an entry is deleted only while its Get
    shows this instance's description (owned). An entry already DELETING is waited for, not deleted again, and one
    still changing settles first (a delete is refused meanwhile). A delete the service marks DELETE_FAILED stops the
    wait at once, with its reason."""
    for i in agentcore_wait.delete_and_wait(dict(entries), lambda i: cp.delete_gateway_rule(gatewayIdentifier=GW, ruleId=i),
                                            get_rule, lambda f: owned(f, own), noun="rule ", why=reason):
        print("deleted", i)


def settle_in_place(full, own=False):
    """full ({rule id: rule}) with every in-flight entry settled in one poll loop (agentcore_wait.settle_in_place)."""
    return agentcore_wait.settle_in_place(full, get_rule, lambda f: owned(f, own), noun="rules ")


def get_rule(rule_id):
    """The rule in full, or None when it is gone (deleted since it was listed)."""
    return agentcore_wait.get_or_none(cp.get_gateway_rule, gatewayIdentifier=GW, ruleId=rule_id)


def get_rules(ids):
    """(id, the rule in full or None when it is gone) for each id, a few reads at a time (agentcore_wait.read_many)."""
    return agentcore_wait.read_many(get_rule, list(ids))


def wait_active(rule_id, grace=0, before=None, missing_grace=0):
    """Wait for the rule to be ACTIVE (agentcore_wait.wait_active: stale statuses straight after an update, DELETING
    and *_FAILED with the reason, and a rule that is gone)."""
    return agentcore_wait.or_exit(agentcore_wait.wait_active, lambda: get_rule(rule_id), f"rule {rule_id}", grace=grace,
                                  before=before, why=reason, missing_grace=missing_grace)


# Healthiest first, so the entry kept (and updated in place) is the one serving traffic now.
RANK = {"ACTIVE": 0, "UPDATING": 1, "CREATING": 2, "UPDATE_FAILED": 3}


def _main():
    if sys.argv[1] == "apply":
        # One GetGatewayRule per entry (DELETING ones too: the list's status can lag), used for status, ordering, the
        # unchanged check and the deletes below alike.
        # A change still in flight is waited out to the status it settles in, so a failure is repaired below (a dead entry
        # deleted, an UPDATE_FAILED rule updated again) instead of stopping the apply.
        full = settle_in_place(read())
        # An entry being deleted (out of band, or settled into DELETING) is never kept; one that never came up
        # (CREATE_FAILED, or a delete that failed) serves nothing. An UPDATE_FAILED rule still serves its last good config,
        # so it is updated in place below, never deleted first, and a healthy entry is the one kept: traffic never runs
        # without the fallback rule. The others are duplicates.
        drop = {i: f for i, f in full.items() if f.get("status") in ("DELETING", "CREATE_FAILED", "DELETE_FAILED")}
        # Healthiest first; among equals, one already matching the spec, so it is kept rather than deleted and the other
        # rewritten.
        found = sorted((f for i, f in full.items() if i not in drop), key=lambda f: (RANK.get(f.get("status"), 9), not same(f)))
        keep, extra = (found[0], found[1:]) if found else (None, [])
        # One delete and wait for all of them: the kept entry's update (or a create) would be refused as a duplicate
        # while they delete.
        if drop or extra:
            delete_all({**drop, **{rid(r): r for r in extra}})
        if keep and same(keep) and keep.get("status") != "UPDATE_FAILED":
            print("unchanged", rid(keep))
            wait_active(rid(keep))
        elif keep:
            # Update in place (UpdateGatewayRule), so traffic never runs without the fallback rule.
            agentcore_wait.send(lambda: cp.update_gateway_rule(gatewayIdentifier=GW, ruleId=rid(keep), **body()),
                                f"rule {rid(keep)} could not be updated")
            print("updated", rid(keep))
            wait_active(rid(keep), grace=3 if keep.get("status") == "UPDATE_FAILED" else 0, before=keep)
        else:
            # Every entry was DELETING or dead and has been waited out above, so the create is not refused as a duplicate.
            r = agentcore_wait.send(lambda: cp.create_gateway_rule(gatewayIdentifier=GW, **body()), "the rule could not be created",
                                    need=rid)
            print("created", rid(r))
            wait_active(rid(r), missing_grace=3)  # a read straight after the create can briefly find nothing
    elif sys.argv[1] == "delete":
        try:
            delete_all(read(own=True), own=True)
        except cp.exceptions.ResourceNotFoundException:
            print("gateway gone: its rules went with it")  # replaced or destroyed out of band
    else:
        raise SystemExit("usage: gateway_rule.py apply|delete")


try:
    _main()
except (RuntimeError, botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError) as e:  # a message, not a traceback
    raise SystemExit(f"gateway_rule.py {sys.argv[1]}: {type(e).__name__}: {e}") from None
