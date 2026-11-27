# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from insights_radar.core.clock import FixedClock
from insights_radar.core.errors import ForbiddenError, UnauthorizedError
from insights_radar.identity.policy import POLICY, Action, Policy
from insights_radar.identity.principal import Principal, PrincipalKind, Role
from insights_radar.identity.run_token import KmsHmac, LocalHmac, RunClaims, RunTokenService

T0 = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)
KEY = b"k" * 32


@pytest.mark.parametrize(
    ("groups", "role"),
    [
        (None, Role.VIEWER),
        ([], Role.VIEWER),
        (["viewer"], Role.VIEWER),
        (["editor"], Role.EDITOR),
        (["admin"], Role.ADMIN),
        (["editor", "admin"], Role.ADMIN),
        ("[admin editor]", Role.ADMIN),
        ("[editor]", Role.EDITOR),
        ("[]", Role.VIEWER),
        ('["admin"]', Role.ADMIN),
        ("admin", Role.ADMIN),
        ("Editor", Role.EDITOR),
        (["unknown"], Role.VIEWER),
        ("editor,viewer", Role.EDITOR),
    ],
)
def test_role_from_groups(groups: object, role: Role) -> None:
    claims: dict[str, object] = {"sub": "u1", "email": "a@b.c"}
    if groups is not None:
        claims["cognito:groups"] = groups
    p = Principal.from_claims(claims)
    assert p.role is role and p.kind is PrincipalKind.USER and p.actor == "user:u1" and p.email == "a@b.c"


@pytest.mark.parametrize("claims", [{}, {"sub": ""}, {"sub": 5}])
def test_missing_subject_is_unauthorized(claims: dict[str, object]) -> None:
    with pytest.raises(UnauthorizedError):
        Principal.from_claims(claims)


def test_module_principal() -> None:
    p = Principal.for_module("scout", "r1", frozenset({"kg_read"}))
    assert p.actor == "module:scout" and not p.is_admin and not p.at_least(Role.VIEWER)
    assert not Policy.allows(p, Action.READ)


ALL = list(Action)


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("action", ALL)
def test_policy_matrix(role: Role, action: Action) -> None:
    p = Principal(PrincipalKind.USER, "u", role)
    expected = action in POLICY[role]
    assert Policy.allows(p, action) is expected
    if expected:
        Policy.require(p, action)
    else:
        with pytest.raises(ForbiddenError):
            Policy.require(p, action)


def test_policy_is_monotonic() -> None:
    assert POLICY[Role.VIEWER] < POLICY[Role.EDITOR] < POLICY[Role.ADMIN]
    assert {Action.USER_MANAGE, Action.MODULE_MANAGE, Action.ANDON_MANAGE}.isdisjoint(POLICY[Role.EDITOR])


def _svc(clock: FixedClock | None = None) -> RunTokenService:
    return RunTokenService(LocalHmac(KEY), clock or FixedClock(T0))


def test_issue_and_verify_roundtrip() -> None:
    svc = _svc()
    tok = svc.issue("scout", "run-1", frozenset({"kg_read", "kg_write_evidence"}), 3600, "prod-1")
    c = svc.verify(tok)
    assert c == RunClaims("scout", "run-1", frozenset({"kg_read", "kg_write_evidence"}), c.exp, "prod-1")
    assert svc.verify(tok) is c  # cached
    assert svc.authorize_tool(tok, "kg_read").module == "scout"


def test_tool_not_granted() -> None:
    svc = _svc()
    tok = svc.issue("signal", "r", frozenset({"kg_read"}), 60)
    with pytest.raises(ForbiddenError):
        svc.authorize_tool(tok, "radar_suggest")


def test_expiry() -> None:
    clock = FixedClock(T0)
    svc = _svc(clock)
    tok = svc.issue("scout", "r", frozenset({"kg_read"}), 60)
    svc.verify(tok)
    clock.advance(timedelta(seconds=61))
    with pytest.raises(UnauthorizedError, match="expired"):
        svc.verify(tok)


@pytest.mark.parametrize("ttl", [0, -1, 8 * 3600 + 1])
def test_ttl_bounds(ttl: int) -> None:
    with pytest.raises(ValueError):
        _svc().issue("m", "r", frozenset(), ttl)


def test_tampered_claims_rejected() -> None:
    svc = _svc()
    tok = svc.issue("signal", "r", frozenset({"kg_read"}), 60)
    _body, mac = tok.split(".")
    forged = RunClaims("signal", "r", frozenset({"kg_read", "radar_suggest"}), 9_999_999_999).to_json()
    forged_b64 = base64.urlsafe_b64encode(forged).rstrip(b"=").decode()
    with pytest.raises(UnauthorizedError, match="signature"):
        svc.verify(forged_b64 + "." + mac)


def test_other_key_rejected() -> None:
    tok = RunTokenService(LocalHmac(b"x" * 32), FixedClock(T0)).issue("m", "r", frozenset(), 60)
    with pytest.raises(UnauthorizedError):
        _svc().verify(tok)


@pytest.mark.parametrize("tok", [None, "", "abc", "a.b.c", "!!!.???", "x" * 9000 + ".y"])
def test_malformed_tokens(tok: str | None) -> None:
    with pytest.raises(UnauthorizedError):
        _svc().verify(tok)


def test_validly_signed_garbage_claims_rejected() -> None:
    mac = LocalHmac(KEY)
    for payload in [b"[]", b'{"module": 1}', b'{"module":"m","run_id":"r","tools":"x","exp":1}', b"not json"]:
        tok = (
            base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
            + "."
            + base64.urlsafe_b64encode(mac.generate(payload)).rstrip(b"=").decode()
        )
        with pytest.raises(UnauthorizedError, match="claims"):
            _svc().verify(tok)


def test_short_key_rejected() -> None:
    with pytest.raises(ValueError):
        LocalHmac(b"short")


def test_cache_eviction() -> None:
    svc = RunTokenService(LocalHmac(KEY), FixedClock(T0), cache_size=2)
    toks = [svc.issue("m", f"r{i}", frozenset(), 60) for i in range(3)]
    for t in toks:
        svc.verify(t)
    assert all(svc.verify(t).run_id.startswith("r") for t in toks)


@mock_aws
def test_kms_hmac_roundtrip() -> None:
    kms = boto3.client("kms", region_name="us-east-1")
    key = kms.create_key(KeySpec="HMAC_256", KeyUsage="GENERATE_VERIFY_MAC")["KeyMetadata"]["KeyId"]
    mac = KmsHmac(kms, key)
    sig = mac.generate(b"hello")
    assert mac.verify(b"hello", sig)
    assert not mac.verify(b"hellp", sig)
    svc = RunTokenService(mac, FixedClock(T0))
    tok = svc.issue("scout", "r", frozenset({"kg_read"}), 120)
    assert svc.verify(tok).module == "scout"


@pytest.mark.parametrize(
    ("caller", "ok"),
    [
        ("arn:aws:sts::111122223333:assumed-role/mr-module-runtime/session-1", True),
        ("arn:aws:sts::444455556666:assumed-role/mr-module-runtime/session-1", False),  # same name, other account
        ("arn:aws:sts::111122223333:assumed-role/mr-module-runtime-evil/s", False),
        ("arn:aws-cn:sts::111122223333:assumed-role/mr-module-runtime/s", False),
        ("x:assumed-role/mr-module-runtime/s", False),
        (None, False),
    ],
)
def test_role_caller_pins_partition_account_and_name(caller: str | None, ok: bool) -> None:
    from insights_radar.identity.principal import RoleCaller

    assert RoleCaller.matches("arn:aws:iam::111122223333:role/mr-module-runtime", caller) is ok
    assert RoleCaller.matches("arn:aws:iam::111122223333:role/service/path/mr-module-runtime", caller) is ok
    assert RoleCaller.matches("", caller) is False


def test_an_email_the_token_marks_unverified_is_ignored_unless_trusted() -> None:
    claims = {"sub": "s1", "email": "Editor@Example.com", "email_verified": "false", "cognito:groups": "[editor]"}
    assert Principal.from_claims(claims).email is None
    assert Principal.from_claims(claims, trust_email=True).email == "Editor@Example.com"
    assert Principal.from_claims({**claims, "email_verified": True}).email == "Editor@Example.com"
    assert Principal.from_claims({"sub": "s1", "email": "a@b.co"}).email == "a@b.co"
    # With an identity provider the address is the provider's: no claim is not a verified claim.
    assert Principal.from_claims({"sub": "s1", "email": "a@b.co"}, federated=True).email is None
    assert Principal.from_claims({"sub": "s1", "email": "a@b.co"}, federated=True, trust_email=True).email == "a@b.co"
