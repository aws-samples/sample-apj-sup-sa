# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Live end-to-end tests against the deployment described by ``build/outputs.json``.

Run after ``deployment/deploy.sh`` with ``AIR_E2E_SIGN_IN=true``:
``cd e2e && AIR_E2E_TARGET=<stack> AWS_PROFILE=<profile> uv run pytest tests -q``.
Test users (viewer, editor, admin) are created per run in the deployment's user pool with suppressed
invitations and deleted when the run ends; their TOTP secrets stay in ``~/.cache/insights-radar-e2e/``
for the run. See the implementation guide, End-to-end tests.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from session import Api, Deployment

ROLES = ("viewer", "editor", "admin")


@pytest.fixture(scope="session")
def deployment() -> Iterator[Deployment]:
    d = Deployment()
    yield d
    # No standing test admin: the e2e users (and their cached secrets) go when the run ends.
    if os.environ.get("AIR_E2E_KEEP_USERS") != "1":
        d.remove_test_users()


@pytest.fixture
def tokens(deployment: Deployment) -> dict[str, str]:
    """Current ID tokens. Not session-scoped: a token lasts an hour and a full run outlasts that, so
    a test that asks for one late must get a fresh one (P2-083). ``sign_in`` reuses a cached token
    while it has 45+ minutes left, so this is a file read, not a sign-in, most of the time."""
    return {role: deployment.sign_in(deployment.user(role))["id"] for role in ROLES}


@pytest.fixture(scope="session")
def api(deployment: Deployment) -> dict[str, Api]:
    """One client per role, each asking for a current token on every request rather than holding the
    one it was built with."""
    clients = {
        role: Api(
            deployment.api_url,
            token=lambda role=role: deployment.sign_in(deployment.user(role))["id"],  # type: ignore[misc]
        )
        for role in ROLES
    }
    clients["anonymous"] = Api(deployment.api_url)
    return clients


@pytest.fixture(scope="session", autouse=True)
def andon_at_start(api: dict[str, Api]) -> dict[str, Any]:
    """The kill switch as the suite found it: the suite never turns AI back on behind an admin.
    autouse, so it is read before the first test runs, not when a later test first asks for it
    (by then the route matrix has already sent PUT /api/andon)."""
    status, body = api["admin"].call("GET", "/api/andon")
    assert status == 200, body
    return dict(body)


@pytest.fixture
def ai_on(andon_at_start: dict[str, Any]) -> None:
    if not andon_at_start["genai_enabled"]:
        pytest.skip("AI is switched off in this deployment; the suite leaves the kill switch alone")
