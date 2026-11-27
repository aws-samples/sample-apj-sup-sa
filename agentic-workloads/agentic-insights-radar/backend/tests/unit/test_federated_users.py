# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Users who sign in through an external identity provider: their username is not their subject."""

from __future__ import annotations

from typing import Any

import boto3
import pytest

from insights_radar.core.errors import NotFoundError
from insights_radar.identity.principal import Principal, Role
from insights_radar.identity.users import LiveRoles, UserAdmin

UNKNOWN_SUB = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
def pool(aws: Any) -> tuple[UserAdmin, Any, str, str]:
    c = boto3.client("cognito-idp", region_name="us-east-1")
    pool_id = c.create_user_pool(PoolName="p")["UserPool"]["Id"]
    for g in ("admin", "editor", "viewer"):
        c.create_group(UserPoolId=pool_id, GroupName=g)
    made = c.admin_create_user(
        UserPoolId=pool_id,
        Username="Federate_alice",
        UserAttributes=[{"Name": "email", "Value": "alice@example.com"}],
        MessageAction="SUPPRESS",
    )
    sub = next(a["Value"] for a in made["User"]["Attributes"] if a["Name"] == "sub")
    c.admin_add_user_to_group(UserPoolId=pool_id, Username="Federate_alice", GroupName="editor")
    return UserAdmin(c, pool_id), c, pool_id, sub


def test_a_federated_user_is_found_by_username_and_by_subject(pool: tuple[UserAdmin, Any, str, str]) -> None:
    users, _, _, sub = pool
    by_name = users.get("Federate_alice")
    assert by_name.username == "Federate_alice" and by_name.subject == sub
    assert by_name.to_public()["subject"] == sub
    by_sub = users.get(sub)
    assert by_sub.username == "Federate_alice" and by_sub.role is Role.EDITOR
    with pytest.raises(NotFoundError):
        users.get(UNKNOWN_SUB)
    with pytest.raises(NotFoundError):
        users.get("Federate_nobody")


def test_the_token_names_the_account_and_live_roles_use_it(pool: tuple[UserAdmin, Any, str, str]) -> None:
    users, c, pool_id, sub = pool
    claims = {"sub": sub, "cognito:username": "Federate_alice", "cognito:groups": ["editor"]}
    p = Principal.from_claims(claims)
    assert p.subject == sub and p.username == "Federate_alice" and p.role is Role.EDITOR
    assert Principal.from_claims({"sub": sub}).username == sub
    live = LiveRoles(users)
    assert live.confirm(p).role is Role.EDITOR
    c.admin_remove_user_from_group(UserPoolId=pool_id, Username="Federate_alice", GroupName="editor")
    assert LiveRoles(users).confirm(p).role is Role.VIEWER
