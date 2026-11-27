# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The deploy option mcpWebIdentity: /iam/api/* routes reached with SigV4 by a session of the stack's MCP
role, mapped to the federated account the person's web sign-in created."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import boto3
import pytest

from insights_radar.api.app import Application
from insights_radar.api.http import Request
from insights_radar.core.clock import FixedClock
from insights_radar.identity.principal import RoleCaller
from insights_radar.identity.users import UserAdmin
from tests.integration.harness import Client, TestContext

MCP_ROLE = "arn:aws:iam::111122223333:role/air-mcp-web-identity"


def _session(name: str, role: str = "air-mcp-web-identity", account: str = "111122223333") -> str:
    return f"arn:aws:sts::{account}:assumed-role/{role}/{name}"


@pytest.fixture
def ctx(graph_table: Any, ops_table: Any, clock: FixedClock) -> TestContext:
    c = TestContext(graph_table, ops_table, clock)
    cognito = boto3.client("cognito-idp", region_name="us-east-1")
    # A federated pool: usernames are <provider>_<id>, as Cognito names users from an identity provider.
    pool = cognito.create_user_pool(PoolName="fed")["UserPool"]["Id"]
    for g in ("admin", "editor", "viewer"):
        cognito.create_group(UserPoolId=pool, GroupName=g)
    for user, group in (
        ("sso_alice", "editor"),
        ("sso_bob", "viewer"),
        ("sso_carol", "admin"),
        ("sso_00u1aBcD", "viewer"),
    ):
        cognito.admin_create_user(
            UserPoolId=pool,
            Username=user,
            UserAttributes=[
                {"Name": "email", "Value": f"{user.split('_', 1)[1]}@example.com"},
                {"Name": "email_verified", "Value": "false" if user == "sso_carol" else "true"},
            ],
            MessageAction="SUPPRESS",
        )
        cognito.admin_add_user_to_group(UserPoolId=pool, Username=user, GroupName=group)
    cognito.admin_disable_user(UserPoolId=pool, Username="sso_bob")
    c.users = UserAdmin(cognito, pool)
    c.settings = replace(
        c.settings,
        user_pool_id=pool,
        identity_provider="SSO",
        mcp_role_arn=MCP_ROLE,
        mcp_issuer="https://issuer.example.com",
        mcp_audience="https://app.example.com",
    )
    return c


def _api(ctx: TestContext) -> Client:
    return Client(Application(ctx))


def test_a_session_of_the_mcp_role_acts_as_its_account(ctx: TestContext) -> None:
    api = _api(ctx)
    status, out, _ = api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("alice"))
    assert status == 200, out
    assert out["role"] == "editor"
    assert out["email"] == "alice@example.com"


def test_a_mixed_case_provider_id_maps_to_its_account(ctx: TestContext) -> None:
    status, out, _ = _api(ctx).call("GET", "/iam/api/me", role="anon", iam_arn=_session("00u1aBcD"))
    assert status == 200, out
    assert out["role"] == "viewer"


def test_the_account_role_is_the_pools_not_the_callers(ctx: TestContext) -> None:
    api = _api(ctx)
    status, _out, _ = api.call("GET", "/iam/api/admin/profile", role="anon", iam_arn=_session("alice"))
    assert status == 403
    status, _out, _ = api.call("GET", "/iam/api/admin/profile", role="anon", iam_arn=_session("carol"))
    assert status == 200


@pytest.mark.parametrize(
    "arn",
    [
        _session("alice", role="some-other-role"),
        _session("alice", account="999999999999"),
        "arn:aws:iam::111122223333:user/alice",
        _session("a"),
        _session("al!ce"),
    ],
)
def test_other_callers_are_refused(ctx: TestContext, arn: str) -> None:
    status, out, _ = _api(ctx).call("GET", "/iam/api/me", role="anon", iam_arn=arn)
    assert status == 401, out


def test_no_iam_caller_on_the_prefix_is_refused(ctx: TestContext) -> None:
    status, _out, _ = _api(ctx).call("GET", "/iam/api/me", role="anon")
    assert status == 401


def test_a_token_on_the_prefix_does_not_count(ctx: TestContext) -> None:
    # A JWT-authorized request never reaches /iam (API Gateway routes it to the IAM authorizer), but if
    # one did, its claims are dropped and it is refused like any other non-role caller.
    status, _out, _ = _api(ctx).call("GET", "/iam/api/me", role="admin")
    assert status == 401


def test_unknown_and_disabled_accounts_are_refused(ctx: TestContext) -> None:
    api = _api(ctx)
    status, out, _ = api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("dave"))
    assert status == 401 and out["code"] == "no_account" and "sign in to the web app once" in out["message"]
    status, out, _ = api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("bob"))
    assert status == 401 and out["code"] == "account_disabled" and "disabled" in out["message"]


def test_off_by_default_refuses_every_iam_request(ctx: TestContext) -> None:
    ctx.settings = replace(ctx.settings, mcp_role_arn="")
    status, _out, _ = _api(ctx).call("GET", "/iam/api/me", role="anon", iam_arn=_session("alice"))
    assert status == 401


def test_without_an_identity_provider_iam_callers_are_refused(ctx: TestContext) -> None:
    ctx.settings = replace(ctx.settings, identity_provider="")
    status, _out, _ = _api(ctx).call("GET", "/iam/api/me", role="anon", iam_arn=_session("alice"))
    assert status == 401


def test_config_advertises_the_option_only_when_on(ctx: TestContext) -> None:
    api = _api(ctx)
    _s, out, _ = api.call("GET", "/api/config", role="anon")
    assert out["mcp_web_identity"] == {
        "role_arn": MCP_ROLE,
        "issuer": "https://issuer.example.com",
        "audience": "https://app.example.com",
    }
    ctx.settings = replace(ctx.settings, mcp_role_arn="")
    _s, out, _ = _api(ctx).call("GET", "/api/config", role="anon")
    assert out["mcp_web_identity"] is None


def test_the_cognito_routes_are_unchanged(ctx: TestContext) -> None:
    status, out, _ = _api(ctx).call("GET", "/api/me", role="viewer")
    assert status == 200, out
    # An IAM caller on the plain /api path has no claims and is not an IAM request: refused.
    status, _out, _ = _api(ctx).call("GET", "/api/me", role="anon", iam_arn=_session("alice"))
    assert status == 401


def test_request_strips_the_prefix() -> None:
    req = Request.from_event({"rawPath": "/iam/api/me", "requestContext": {"http": {"method": "GET"}}})
    assert req.via_iam and req.path == "/api/me" and req.claims == {}
    assert not Request.from_event({"rawPath": "/iamx/api/me", "requestContext": {}}).via_iam


def test_session_name_shape() -> None:
    assert RoleCaller.session_name(MCP_ROLE, _session("a.b_c-9")) == "a.b_c-9"
    # Mixed-case provider ids (Okta 00u..., Entra ID base64url) and emails are STS session names too.
    assert RoleCaller.session_name(MCP_ROLE, _session("00u1aBcD")) == "00u1aBcD"
    assert RoleCaller.session_name(MCP_ROLE, _session("Alice+x=y,z@example.com")) == "Alice+x=y,z@example.com"
    assert RoleCaller.session_name(MCP_ROLE, _session("x" * 64)) == "x" * 64
    assert RoleCaller.session_name(MCP_ROLE, _session("x" * 65)) is None
    assert RoleCaller.session_name(MCP_ROLE, _session("x")) is None  # STS needs at least 2
    assert RoleCaller.session_name(MCP_ROLE, _session("alice\n")) is None  # $ alone would allow it
    assert RoleCaller.session_name(MCP_ROLE, None) is None
    assert RoleCaller.session_name("", _session("alice")) is None


def test_an_unverified_email_counts_only_when_the_provider_is_trusted(ctx: TestContext) -> None:
    # As on the browser path: carol's pool address is not verified, so it is no email of hers...
    status, out, _ = _api(ctx).call("GET", "/iam/api/me", role="anon", iam_arn=_session("carol"))
    assert status == 200 and out["email"] is None
    # ...unless the deployment trusts the provider's addresses (oidcTrustEmail).
    ctx.settings = replace(ctx.settings, trust_provider_email=True)
    status, out, _ = _api(ctx).call("GET", "/iam/api/me", role="anon", iam_arn=_session("carol"))
    assert status == 200 and out["email"] == "carol@example.com"


def test_an_account_is_looked_up_once_per_ttl(ctx: TestContext, monkeypatch: pytest.MonkeyPatch) -> None:
    import insights_radar.api.routes as routes

    calls: list[str] = []
    real = ctx.users.get

    def counted(name: str) -> Any:
        calls.append(name)
        return real(name)

    monkeypatch.setattr(ctx.users, "get", counted)
    clock = [1000.0]
    monkeypatch.setattr(routes.time, "monotonic", lambda: clock[0])
    api = _api(ctx)
    for _ in range(3):
        assert api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("alice"))[0] == 200
    assert calls == ["sso_alice"]
    clock[0] += routes.LIVE_ROLE_TTL + 1
    assert api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("alice"))[0] == 200
    assert calls == ["sso_alice", "sso_alice"]


def test_a_disabled_account_is_dropped_from_the_cache(ctx: TestContext, monkeypatch: pytest.MonkeyPatch) -> None:
    import insights_radar.api.routes as routes

    clock = [1000.0]
    monkeypatch.setattr(routes.time, "monotonic", lambda: clock[0])
    api = _api(ctx)
    assert api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("alice"))[0] == 200
    ctx.cognito.admin_disable_user(UserPoolId=ctx.settings.user_pool_id, Username="sso_alice")
    clock[0] += routes.LIVE_ROLE_TTL + 1
    assert api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("alice"))[0] == 401


def test_the_cache_is_bounded(ctx: TestContext, monkeypatch: pytest.MonkeyPatch) -> None:
    import insights_radar.api.routes as routes

    monkeypatch.setattr(routes, "IAM_SEEN_MAX", 1)
    api = _api(ctx)
    assert api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("alice"))[0] == 200
    assert api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("carol"))[0] == 200
    assert api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("alice"))[0] == 200


def test_refreshing_a_cached_account_keeps_the_others(ctx: TestContext, monkeypatch: pytest.MonkeyPatch) -> None:
    import insights_radar.api.routes as routes

    calls: list[str] = []
    real = ctx.users.get

    def counted(name: str) -> Any:
        calls.append(name)
        return real(name)

    monkeypatch.setattr(ctx.users, "get", counted)
    monkeypatch.setattr(routes, "IAM_SEEN_MAX", 3)
    ttl = routes.LIVE_ROLE_TTL
    clock = [1000.0]
    monkeypatch.setattr(routes.time, "monotonic", lambda: clock[0])
    api = _api(ctx)

    def me(name: str, at: float) -> None:
        clock[0] = 1000.0 + at
        assert api.call("GET", "/iam/api/me", role="anon", iam_arn=_session(name))[0] == 200

    me("alice", 0)
    me("carol", 10)
    me("alice", ttl + 1)  # alice refreshed: now the newest entry
    me("00u1aBcD", ttl + 2)  # the cache is full
    me("carol", ttl + 11)  # carol refreshed: her own entry goes, not alice's
    me("alice", ttl + 12)  # still cached
    assert calls == ["sso_alice", "sso_carol", "sso_alice", "sso_00u1aBcD", "sso_carol"]


def _throttle(name: str) -> Any:
    from botocore.exceptions import ClientError

    raise ClientError({"Error": {"Code": "TooManyRequestsException", "Message": "slow down"}}, "AdminGetUser")


def test_cognito_throttling_keeps_a_recent_account_as_a_viewer(
    ctx: TestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    import insights_radar.api.routes as routes
    from insights_radar.identity.users import THROTTLE_GRACE_SECONDS

    clock = [1000.0]
    monkeypatch.setattr(routes.time, "monotonic", lambda: clock[0])
    api = _api(ctx)
    status, out, _ = api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("carol"))
    assert status == 200 and out["role"] == "admin"
    monkeypatch.setattr(ctx.users, "get", _throttle)
    clock[0] += routes.LIVE_ROLE_TTL + 1
    status, out, _ = api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("carol"))
    assert status == 200 and out["role"] == "viewer"  # unconfirmed: never more than a viewer
    clock[0] += THROTTLE_GRACE_SECONDS
    status, _out, _ = api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("carol"))
    assert status != 200  # past the grace period the throttling error stands
    # A caller never seen before has nothing to fall back on.
    status, _out, _ = api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("alice"))
    assert status != 200


def test_other_cognito_errors_are_not_softened(ctx: TestContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from botocore.exceptions import ClientError

    import insights_radar.api.routes as routes

    def broken(name: str) -> Any:
        raise ClientError({"Error": {"Code": "InternalErrorException", "Message": "x"}}, "AdminGetUser")

    clock = [1000.0]
    monkeypatch.setattr(routes.time, "monotonic", lambda: clock[0])
    api = _api(ctx)
    assert api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("alice"))[0] == 200
    monkeypatch.setattr(ctx.users, "get", broken)
    clock[0] += routes.LIVE_ROLE_TTL + 1
    assert api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("alice"))[0] != 200


def test_a_deleted_account_gets_no_throttle_grace(ctx: TestContext, monkeypatch: pytest.MonkeyPatch) -> None:
    import insights_radar.api.routes as routes

    clock = [1000.0]
    monkeypatch.setattr(routes.time, "monotonic", lambda: clock[0])
    api = _api(ctx)
    assert api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("alice"))[0] == 200
    ctx.cognito.admin_delete_user(UserPoolId=ctx.settings.user_pool_id, Username="sso_alice")
    clock[0] += routes.LIVE_ROLE_TTL + 1
    status, out, _ = api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("alice"))
    assert status == 401 and out["code"] == "no_account"
    monkeypatch.setattr(ctx.users, "get", _throttle)
    clock[0] += 1
    assert api.call("GET", "/iam/api/me", role="anon", iam_arn=_session("alice"))[0] != 200
