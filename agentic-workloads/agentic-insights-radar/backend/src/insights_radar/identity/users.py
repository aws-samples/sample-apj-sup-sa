# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""User administration over the Cognito user pool.

Admins invite users (Cognito sends the temporary password), change their group, disable,
enable or delete them. The service refuses to remove, demote or disable the last enabled admin
so the workspace can never lock itself out (the internal radar's sec. 29.3 rule).
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from botocore.exceptions import ClientError

from insights_radar.core.errors import AccountDisabledError, ConflictError, NotFoundError, ValidationError
from insights_radar.identity.principal import Principal, PrincipalKind, Role

# What Cognito answers for a PaginationToken it cannot read (the name differs by API version).
_BAD_TOKEN_CODES = frozenset({"InvalidParameterException", "InvalidToken", "InvalidTokenException"})

# RFC 5322 dot-atom local part (no quotes, angle brackets or spaces) at a DNS host name: an address
# goes into Cognito, the invitation email and audit rows, so it must never carry markup.
_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
EMAIL_RE = re.compile(
    rf"^(?=.{{1,254}}$)(?=[^@]{{1,64}}@){_ATOM}(?:\.{_ATOM})*@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{{0,61}}[A-Za-z0-9])?\.)+[A-Za-z]{{2,63}}$"
)
_EMAIL_RE = EMAIL_RE
GROUP_ADD_ATTEMPTS = 3
USERNAME_MAX = 128  # Cognito's limit; botocore refuses longer (or empty) before sending
CACHE_MAX = 10_000
THROTTLE_GRACE_SECONDS = 300.0


_SUB_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
LAST_ADMIN = "this is the last administrator; add another admin first"


@dataclass(frozen=True, slots=True)
class UserView:
    username: str
    email: str
    role: Role
    enabled: bool
    status: str
    created_at: str
    # The token's ``sub``. For a user who signs in with a password it is the username; for a user who signs
    # in through an external identity provider the username is ``<provider>_<id>`` and differs.
    subject: str = ""
    # The pool's email_verified attribute as Cognito stores it ("true", "false", or "" when absent).
    email_verified: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "subject": self.subject or self.username,
            "email": self.email,
            "role": self.role.value,
            "enabled": self.enabled,
            "status": self.status,
            "created_at": self.created_at,
        }


class UserAdmin:
    def __init__(self, cognito: Any, pool_id: str) -> None:
        self._c = cognito
        self._pool = pool_id
        self._group_cache: tuple[float, set[str], set[str]] | None = None

    def _groups(self, username: str) -> set[str]:
        res = self._c.admin_list_groups_for_user(UserPoolId=self._pool, Username=username)
        return {g["GroupName"] for g in res.get("Groups", [])}

    @staticmethod
    def _role_of(groups: set[str]) -> Role:
        for r in (Role.ADMIN, Role.EDITOR):
            if r.value in groups:
                return r
        return Role.VIEWER

    def _members(self, group: str) -> set[str]:
        names: set[str] = set()
        kwargs: dict[str, Any] = {"UserPoolId": self._pool, "GroupName": group, "Limit": 60}
        while True:
            res = self._c.list_users_in_group(**kwargs)
            names.update(u["Username"] for u in res.get("Users", []))
            if not res.get("NextToken"):
                return names
            kwargs["NextToken"] = res["NextToken"]

    def _view(self, raw: dict[str, Any], role: Role | None = None) -> UserView:
        attrs = {a["Name"]: a["Value"] for a in raw.get("Attributes", raw.get("UserAttributes", []))}
        created: Any = raw.get("UserCreateDate")
        created_text = (
            created.isoformat() if created is not None and hasattr(created, "isoformat") else str(created or "")
        )
        return UserView(
            raw["Username"],
            attrs.get("email", ""),
            role or self._role_of(self._groups(raw["Username"])),
            bool(raw.get("Enabled", True)),
            str(raw.get("UserStatus", "")),
            created_text,
            attrs.get("sub", raw["Username"]),
            attrs.get("email_verified", ""),
        )

    def list_page(self, limit: int = 60, cursor: str | None = None) -> tuple[list[UserView], str | None]:
        """Up to ``limit`` users and Cognito's token for the rest (None at the end), so every user of a
        large pool can be listed."""
        # Two group listings instead of one AdminListGroupsForUser call per user, cached briefly: a
        # large pool paid O(group size) Cognito calls for every page and risked throttling LiveRoles.
        admins, editors = self._cached_members()

        def role(name: str) -> Role:
            return Role.ADMIN if name in admins else Role.EDITOR if name in editors else Role.VIEWER

        out: list[UserView] = []
        kwargs: dict[str, Any] = {"UserPoolId": self._pool}
        token = cursor
        while len(out) < limit:
            # Never ask for more than still fits, so the token points exactly after the last user returned.
            kwargs["Limit"] = min(60, limit - len(out))
            if token:
                kwargs["PaginationToken"] = token
            try:
                res = self._c.list_users(**kwargs)
            except ClientError as err:
                # A garbage cursor is the client's mistake, not a fault: the pass-through of Cognito's
                # PaginationToken used to reach the caller as a 500.
                if err.response["Error"]["Code"] not in _BAD_TOKEN_CODES:
                    raise
                raise ValidationError("cursor is invalid") from err
            out.extend(self._view(u, role(u["Username"])) for u in res.get("Users", []))
            token = res.get("PaginationToken")
            if not token:
                break
        return out, token

    GROUP_CACHE_SECONDS = 15

    def _cached_members(self) -> tuple[set[str], set[str]]:
        now = time.monotonic()
        if self._group_cache is None or now - self._group_cache[0] > self.GROUP_CACHE_SECONDS:
            self._group_cache = (now, self._members(Role.ADMIN.value), self._members(Role.EDITOR.value))
        return self._group_cache[1], self._group_cache[2]

    def get(self, username: str) -> UserView:
        if not 1 <= len(username) <= USERNAME_MAX:
            # botocore refuses it before sending, with a ParamValidationError that is no ClientError: an
            # empty owner subject reached the caller as a 500 (B3-037).
            raise ValidationError(f"a username is 1-{USERNAME_MAX} characters")
        try:
            raw = self._c.admin_get_user(UserPoolId=self._pool, Username=username)
        except self._c.exceptions.UserNotFoundException as err:
            # A federated user's subject (stored as a radar owner) is not their username: look it up.
            if not _SUB_RE.fullmatch(username):
                raise NotFoundError("user not found") from err
            res = self._c.list_users(UserPoolId=self._pool, Filter=f'sub = "{username}"', Limit=1)
            found = res.get("Users", [])
            if not found:
                raise NotFoundError("user not found") from err
            return self._view(found[0])
        return self._view(raw)

    def has_account(self, email: str) -> bool:
        """Whether an enabled user signs in with ``email``: a radar owner who is no administrator cannot
        read the user list, so a mistyped editor address was saved without a word (CTR HCB-037)."""
        # Cognito matches the value exactly, and the pool stores addresses as typed at sign-up.
        for each in dict.fromkeys((email, email.lower())):
            quoted = each.replace("\\", "\\\\").replace('"', '\\"')
            res = self._c.list_users(UserPoolId=self._pool, Filter=f'email = "{quoted}"', Limit=10)
            if any(bool(u.get("Enabled", True)) for u in res.get("Users", [])):
                return True
        return False

    def _forget_groups(self) -> None:
        """The list's group cache is stale once a role changes: the admin who changed one saw the old
        role in the list for 15 seconds (B3-040)."""
        self._group_cache = None

    def _enabled_admins(self) -> list[str]:
        out: list[str] = []
        kwargs: dict[str, Any] = {"UserPoolId": self._pool, "GroupName": Role.ADMIN.value, "Limit": 60}
        while True:
            res = self._c.list_users_in_group(**kwargs)
            out.extend(u["Username"] for u in res.get("Users", []) if u.get("Enabled", True))
            if not res.get("NextToken"):
                return out
            kwargs["NextToken"] = res["NextToken"]

    def _guard_last_admin(self, username: str) -> None:
        """``username`` is the canonical Cognito username (a UUID: the pool signs in by email), never the
        email the request may have used, which would never match the group listing."""
        if set(self._enabled_admins()) <= {username}:
            raise ConflictError(LAST_ADMIN, {"code": "last_admin"})

    def _restore_if_no_admin_left(self, undo: Any) -> None:
        """Two admins demoting each other at once can both pass the guard; whichever change lands last
        sees no enabled admin left, undoes itself and is refused."""
        if not self._enabled_admins():
            undo()
            raise ConflictError(LAST_ADMIN, {"code": "last_admin"})

    def invite(self, email: str, role: Role) -> UserView:
        email = email.strip().lower()
        if not _EMAIL_RE.fullmatch(email):
            raise ValidationError("a valid email address is required")
        try:
            res = self._c.admin_create_user(
                UserPoolId=self._pool,
                Username=email,
                DesiredDeliveryMediums=["EMAIL"],
                UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
            )
        except self._c.exceptions.UsernameExistsException as err:
            raise ConflictError("a user with that email already exists") from err
        username = res["User"]["Username"]
        if role is not Role.VIEWER:
            self._forget_groups()
            self._add_or_withdraw(username, role)
        return self.get(username)

    def _add_or_withdraw(self, username: str, role: Role) -> None:
        """Put a new user in their group, retrying; if that keeps failing, never leave them invited with the
        wrong role: remove the account (its temporary password stops working) and let the admin retry."""
        attempt = 0
        while True:
            try:
                self._c.admin_add_user_to_group(UserPoolId=self._pool, Username=username, GroupName=role.value)
                return
            except Exception:
                attempt += 1
                if attempt >= GROUP_ADD_ATTEMPTS:
                    self._c.admin_delete_user(UserPoolId=self._pool, Username=username)
                    raise

    def set_role(self, username: str, role: Role) -> UserView:
        current = self.get(username)
        username = current.username
        self._forget_groups()
        demoting = current.role is Role.ADMIN and role is not Role.ADMIN
        if demoting:
            self._guard_last_admin(username)
        for g in self._groups(username) & {Role.ADMIN.value, Role.EDITOR.value}:
            if g != role.value:
                self._c.admin_remove_user_from_group(UserPoolId=self._pool, Username=username, GroupName=g)
        if role is not Role.VIEWER:
            self._c.admin_add_user_to_group(UserPoolId=self._pool, Username=username, GroupName=role.value)
        if current.role is not role:
            # Tokens carry the role: end the user's sessions so they sign in again with the new one.
            self._c.admin_user_global_sign_out(UserPoolId=self._pool, Username=username)
        if demoting:
            self._restore_if_no_admin_left(lambda: self._add_to_admins(username))
        return self.get(username)

    def _add_to_admins(self, username: str) -> None:
        self._c.admin_add_user_to_group(UserPoolId=self._pool, Username=username, GroupName=Role.ADMIN.value)

    def set_enabled(self, username: str, enabled: bool) -> UserView:
        current = self.get(username)
        username = current.username
        if not enabled and current.role is Role.ADMIN:
            self._guard_last_admin(username)
        if enabled:
            self._c.admin_enable_user(UserPoolId=self._pool, Username=username)
        else:
            self._c.admin_disable_user(UserPoolId=self._pool, Username=username)
            if current.role is Role.ADMIN:
                self._restore_if_no_admin_left(
                    lambda: self._c.admin_enable_user(UserPoolId=self._pool, Username=username)
                )
            self._c.admin_user_global_sign_out(UserPoolId=self._pool, Username=username)
        return self.get(username)

    def delete(self, username: str) -> None:
        current = self.get(username)
        username = current.username
        self._forget_groups()
        if current.role is Role.ADMIN:
            self._guard_last_admin(username)
            # Leave the admin group first, so a concurrent demotion of the other admin is caught before
            # anything irreversible happens.
            self._c.admin_remove_user_from_group(UserPoolId=self._pool, Username=username, GroupName=Role.ADMIN.value)
            self._restore_if_no_admin_left(lambda: self._add_to_admins(username))
        self._c.admin_delete_user(UserPoolId=self._pool, Username=username)


_RANK = {Role.VIEWER: 0, Role.EDITOR: 1, Role.ADMIN: 2}


class LiveRoles:
    """Confirms every user token against Cognito, so a demotion, a disabled account or a deleted one
    takes effect within ``ttl`` rather than when the ID token (valid for an hour) expires. A global
    sign-out revokes refresh tokens only, so the ID token has to be checked here. Lookups cost one
    call per user per ``ttl`` at most."""

    def __init__(self, users: UserAdmin, ttl: float = 30.0, now: Callable[[], float] = time.monotonic) -> None:
        self._users = users
        self._ttl = ttl
        self._now = now
        self._seen: OrderedDict[str, tuple[float, Role, bool]] = OrderedDict()

    def confirm(self, principal: Principal) -> Principal:
        if principal.kind is not PrincipalKind.USER:
            return principal
        name = principal.username or principal.subject
        hit = self._seen.get(name)
        stale = False
        if hit is None or self._now() - hit[0] > self._ttl:
            try:
                view = self._users.get(name)
                hit = (self._now(), view.role, view.enabled)
                self._seen[name] = hit
            except NotFoundError as err:
                raise AccountDisabledError("this account no longer exists") from err
            except ClientError as err:
                # Cognito throttling must not turn every request into a 500: keep the last confirmed
                # answer for a bounded grace period, but only as a viewer. A role that cannot be confirmed
                # is not trusted, or a just-demoted administrator could keep acting by causing throttling.
                code = err.response.get("Error", {}).get("Code")
                if code not in ("TooManyRequestsException", "ThrottlingException") or hit is None:
                    raise
                if self._now() - hit[0] > self._ttl + THROTTLE_GRACE_SECONDS:
                    raise
                stale = True
                if not hit[2]:
                    # The last answer said the account was disabled: never soften that.
                    raise AccountDisabledError("this account is disabled") from err
            self._seen.move_to_end(name)
            while len(self._seen) > CACHE_MAX:  # bounded: one entry per active user
                self._seen.popitem(last=False)
        _, role, enabled = hit
        if not enabled:
            raise AccountDisabledError("this account is disabled")
        if stale:
            role = Role.VIEWER
        if _RANK[role] < _RANK[principal.role]:
            return replace(principal, role=role)  # the token still says the old, higher role
        return principal
