# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Every API route, as every role, against the live deployment.

Each call uses an id that does not exist or a body that fails validation, so an allowed call ends
in 2xx/400/404 without changing anything, and a refused call must be 403. The expectations come
from the role table (backend/src/insights_radar/identity/policy.py), not from the responses.
"""

from __future__ import annotations

from typing import Any

import pytest

from session import Api, unsigned_jwt

from .conftest import ROLES

VIEWER = {"read", "chat"}
EDITOR = VIEWER | {"feedback", "working", "draft", "radar.create", "radar.edit", "suggestion", "product.read"}
ALLOWED = {"viewer": VIEWER, "editor": EDITOR, "admin": None}  # None: everything

NODE = "topic/00000000-0000-7000-8000-000000000000"
PID, DID, SLUG, MODULE, USER, SID, KEY = (
    "nosuchproduct0", "nosuchdoc0", "no-such-radar", "no-such-module", "no-such-user", "nosuchsrc0", "nosuchkey")

# (method, path, body, action). action None: any signed-in user.
ROUTES: list[tuple[str, str, Any, str | None]] = [
    ("GET", "/api/me", None, None),
    ("GET", "/api/nav", None, "read"),
    ("GET", "/api/chat/status", None, "chat"),
    ("GET", "/api/chat/settings", None, "budget"),
    ("PUT", "/api/chat/settings", {"daily_tokens": "lots"}, "budget"),
    ("GET", "/api/users", None, "user"),
    ("POST", "/api/users", {"email": "not-an-email", "role": "viewer"}, "user"),
    ("PATCH", f"/api/users/{USER}", {"role": "viewer"}, "user"),
    ("DELETE", f"/api/users/{USER}", None, "user"),
    ("GET", "/api/products", None, "read"),
    ("POST", "/api/products", {}, "product"),
    ("GET", f"/api/products/{PID}", None, "read"),
    ("PATCH", f"/api/products/{PID}", {"summary": "x", "version": 1}, "product"),
    ("DELETE", f"/api/products/{PID}", None, "product"),
    ("POST", f"/api/products/{PID}/sync", {}, "product"),
    ("POST", f"/api/products/{PID}/ask", {"query": "warranty terms"}, "product.read"),
    ("GET", f"/api/products/{PID}/documents", None, "product.read"),
    ("POST", f"/api/products/{PID}/documents", {"filename": "a.pdf", "size": 10}, "product"),
    ("POST", f"/api/products/{PID}/documents/{DID}/complete", {}, "product"),
    ("DELETE", f"/api/products/{PID}/documents/{DID}", None, "product"),
    ("GET", "/api/models", None, "read"),
    ("GET", "/api/modules", None, "read"),
    ("POST", "/api/modules", {}, "module"),
    ("GET", f"/api/modules/{MODULE}", None, "read"),
    ("PATCH", f"/api/modules/{MODULE}", {"enabled": True, "version": 1}, "module"),
    ("DELETE", f"/api/modules/{MODULE}", None, "module"),
    ("POST", f"/api/modules/{MODULE}/run", {}, "run"),
    ("GET", f"/api/modules/{MODULE}/runs", None, "health"),
    ("GET", "/api/runs", None, "health"),
    ("GET", f"/api/modules/{MODULE}/sources", None, "read"),
    ("POST", f"/api/modules/{MODULE}/sources", {}, "module"),
    ("PATCH", f"/api/modules/{MODULE}/sources/{SID}", {"enabled": False}, "module"),
    ("DELETE", f"/api/modules/{MODULE}/sources/{SID}", None, "module"),
    ("GET", "/api/andon", None, None),  # whether AI is on is public to users; who switched it is not
    ("PUT", "/api/andon", {"genai_enabled": "maybe"}, "andon"),
    ("GET", "/api/graph/schema", None, "read"),
    ("POST", "/api/graph/search", {"query": "standing desk"}, "read"),
    ("GET", f"/api/graph/nodes/{NODE}", None, "read"),
    ("DELETE", f"/api/graph/nodes/{NODE}", None, "graph"),
    ("GET", f"/api/graph/nodes/{NODE}/neighbors", None, "read"),
    ("GET", "/api/graph/explore", None, "read"),
    ("GET", "/api/insights/signals", None, "read"),
    ("GET", f"/api/insights/nodes/{NODE}", None, "read"),
    ("POST", f"/api/nodes/{NODE}/feedback", {"vote": "up"}, "feedback"),
    ("DELETE", f"/api/nodes/{NODE}/feedback", None, "feedback"),
    ("GET", "/api/feedback", None, "audit"),
    ("PUT", f"/api/nodes/{NODE}/working", {}, "working"),
    ("DELETE", f"/api/nodes/{NODE}/working", None, "working"),
    ("GET", f"/api/drafts/{NODE}", None, "read"),
    ("POST", f"/api/nodes/{NODE}/draft", {}, "draft"),
    ("GET", "/api/radar-templates", None, "read"),
    ("GET", "/api/radars", None, "read"),
    ("POST", "/api/radars", {}, "radar.create"),
    ("GET", f"/api/radars/{SLUG}", None, "read"),
    ("PATCH", f"/api/radars/{SLUG}", {"name": "x", "version": 1}, "radar.edit"),
    ("PUT", f"/api/radars/{SLUG}/entries", {"entries": [], "version": 1}, "radar.edit"),
    ("POST", f"/api/radars/{SLUG}/import", {"format": "csv", "text": "", "version": 1}, "radar.edit"),
    ("GET", f"/api/radars/{SLUG}/export", None, "read"),
    ("GET", f"/api/radars/{SLUG}/template", None, "read"),
    ("GET", f"/api/radars/{SLUG}/versions", None, "read"),
    ("POST", f"/api/radars/{SLUG}/rollback", {"version": 1}, "radar.edit"),
    ("GET", f"/api/radars/{SLUG}/diff", None, "read"),
    ("PUT", f"/api/radars/{SLUG}/schema", {}, "radar.edit"),
    ("PUT", f"/api/radars/{SLUG}/access", {}, "radar.edit"),
    ("DELETE", f"/api/radars/{SLUG}", None, "radar.edit"),
    ("GET", f"/api/radars/{SLUG}/suggestions", None, "read"),
    ("POST", f"/api/radars/{SLUG}/suggestions/{KEY}/accept", {}, "suggestion"),
    ("DELETE", f"/api/radars/{SLUG}/suggestions/{KEY}", None, "suggestion"),
    ("GET", "/api/audit", None, "audit"),
    ("GET", "/api/health", None, "health"),
]


def allowed(role: str, action: str | None) -> bool:
    granted = ALLOWED[role]
    return action is None or granted is None or action in granted


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(("method", "path", "body", "action"), ROUTES, ids=[f"{m} {p}" for m, p, _, _ in ROUTES])
def test_route_obeys_the_role_table(api: dict[str, Api], role: str, method: str, path: str, body: Any,
                                    action: str | None) -> None:
    status, payload = api[role].call(method, path, body)
    if allowed(role, action):
        assert status not in (401, 403), payload
        assert status < 500, payload
    else:
        assert status == 403, payload


@pytest.mark.parametrize(("method", "path", "body", "action"), ROUTES, ids=[f"{m} {p}" for m, p, _, _ in ROUTES])
def test_anonymous_callers_are_refused_everywhere(api: dict[str, Api], method: str, path: str, body: Any,
                                                  action: str | None) -> None:
    assert api["anonymous"].call(method, path, body)[0] == 401


def test_public_config_needs_no_sign_in(api: dict[str, Api]) -> None:
    status, config = api["anonymous"].call("GET", "/api/config")
    assert status == 200 and {"user_pool_id", "user_pool_client_id"} <= set(config)


def test_a_forged_token_is_refused(deployment: Any) -> None:
    forged = unsigned_jwt({"sub": "x", "cognito:groups": ["admin"]})
    assert Api(deployment.api_url, forged).call("GET", "/api/users")[0] == 401


def test_the_safe_bodies_left_the_kill_switch_and_chat_budget_alone(
    api: dict[str, Api], andon_at_start: dict[str, Any]
) -> None:
    assert api["admin"].call("GET", "/api/andon")[1]["genai_enabled"] is andon_at_start["genai_enabled"]
    status, limits = api["admin"].call("GET", "/api/chat/settings")
    assert status == 200 and isinstance(limits["daily_tokens"], int)


def test_only_admins_see_who_switched_the_kill_switch(api: dict[str, Api]) -> None:
    for role in ("viewer", "editor"):
        assert set(api[role].call("GET", "/api/andon")[1]) == {"genai_enabled"}
    assert {"reason", "updated_by"} <= set(api["admin"].call("GET", "/api/andon")[1])
