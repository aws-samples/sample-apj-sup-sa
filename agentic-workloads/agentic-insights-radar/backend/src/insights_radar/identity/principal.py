# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Who is calling: a signed-in user (Cognito) or a module run (run token).

API Gateway's HTTP API JWT authorizer hands claims to the Lambda as strings; array claims
such as ``cognito:groups`` arrive either as a real list (REST/test paths) or as the string
``"[admin editor]"`` (HTTP API). Both shapes are accepted. The role is always derived on the
server from verified claims, never from the request body.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from insights_radar.core.errors import UnauthorizedError


class Role(StrEnum):
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"

    @property
    def rank(self) -> int:
        return {Role.VIEWER: 0, Role.EDITOR: 1, Role.ADMIN: 2}[self]


class PrincipalKind(StrEnum):
    USER = "user"
    MODULE = "module"


def _parse_groups(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(g) for g in raw]
    text = str(raw).strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        try:
            return [str(g) for g in json.loads(text)]  # '["a","b"]': bracketed JSON is always a list
        except json.JSONDecodeError:
            pass
        return [g.strip().strip('"') for g in inner.replace(",", " ").split() if g.strip()]
    return [g for g in text.replace(",", " ").split() if g]


@dataclass(frozen=True, slots=True)
class Principal:
    kind: PrincipalKind
    subject: str
    role: Role
    email: str | None = None
    module_slug: str | None = None
    run_id: str | None = None
    # The Cognito username (``cognito:username``): the subject for password users, ``<provider>_<id>`` for
    # federated ones. User administration and live role checks address the account by it.
    username: str | None = None
    tools: frozenset[str] = field(default_factory=frozenset)

    @property
    def actor(self) -> str:
        """Stable string stamped into ``created_by`` and audit rows."""
        if self.kind is PrincipalKind.MODULE:
            return f"module:{self.module_slug}"
        return f"user:{self.subject}"

    @property
    def is_admin(self) -> bool:
        return self.kind is PrincipalKind.USER and self.role is Role.ADMIN

    def at_least(self, role: Role) -> bool:
        return self.kind is PrincipalKind.USER and self.role.rank >= role.rank

    @classmethod
    def from_claims(cls, claims: dict[str, Any], *, trust_email: bool = False, federated: bool = False) -> Principal:
        """``trust_email`` takes the email even when the token says it is not verified: for an identity
        provider that vouches for its addresses but does not send ``email_verified`` (deploy option
        ``oidcTrustEmail``). Otherwise an address marked unverified is ignored, so it cannot match a
        radar's editor list: a federated user's email is copied from the provider at each sign-in."""
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise UnauthorizedError("token has no subject")
        groups = {g.lower() for g in _parse_groups(claims.get("cognito:groups"))}
        role = Role.VIEWER
        for candidate in (Role.ADMIN, Role.EDITOR):
            if candidate.value in groups:
                role = candidate
                break
        email = claims.get("email")
        # email_verified is a JSON boolean, a string once API Gateway has passed the claims on. A password
        # pool verifies every address it invites, so there a missing claim means verified; with an identity
        # provider the address is the provider's, so a missing claim counts as unverified (as in the
        # sign-in hook).
        default = "false" if federated else "true"
        if not trust_email and str(claims.get("email_verified", default)).lower() != "true":
            email = None
        username = claims.get("cognito:username")
        return cls(
            kind=PrincipalKind.USER,
            subject=sub,
            role=role,
            email=email if isinstance(email, str) else None,
            username=username if isinstance(username, str) and username else sub,
        )

    @classmethod
    def for_module(cls, module_slug: str, run_id: str, tools: frozenset[str]) -> Principal:
        return cls(
            kind=PrincipalKind.MODULE,
            subject=f"{module_slug}/{run_id}",
            role=Role.VIEWER,
            module_slug=module_slug,
            run_id=run_id,
            tools=tools,
        )


class RoleCaller:
    """Whether an IAM caller is a session of one role in this account.

    HTTP APIs have no resource policy, so a SigV4 request from any account passes the IAM
    authorizer; the caller's assumed-role ARN must match the expected role's partition, account and
    name exactly, not just contain the role name.
    """

    _ROLE = re.compile(
        r"^arn:(?P<partition>aws[a-z-]*):iam::(?P<account>\d{12}):role/(?:[\w+=,.@-]+/)*(?P<name>[\w+=,.@-]+)$"
    )

    @classmethod
    def matches(cls, role_arn: str, caller_arn: str | None) -> bool:
        m = cls._ROLE.match(role_arn or "")
        if not m or not caller_arn:
            return False
        return caller_arn.startswith(f"arn:{m['partition']}:sts::{m['account']}:assumed-role/{m['name']}/")

    # An identity provider's user id as Cognito names a federated user (``<provider>_<id>``): the role's
    # trust policy makes the session name equal the web identity token's subject, so this is that id.
    # STS's own RoleSessionName alphabet and length, so any subject STS accepts (mixed-case ids such as
    # Okta's or Entra ID's included) maps; the user pool's case-insensitive usernames fold case already.
    _SESSION = re.compile(r"^[A-Za-z0-9_+=,.@-]{2,64}$")

    @classmethod
    def session_name(cls, role_arn: str, caller_arn: str | None) -> str | None:
        """The session name of a caller that :meth:`matches` the role, or None: 2 to 64 characters from
        STS's session-name alphabet (letters, digits and ``_+=,.@-``)."""
        if not cls.matches(role_arn, caller_arn):
            return None
        name = str(caller_arn).rsplit("/", 1)[-1]
        return name if cls._SESSION.fullmatch(name) else None
