# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Per-radar access, live (CTR 6786394c): the role matrix uses ids that do not exist and one editor, so
a radar's own owner and editors list was never exercised against a deployment. A second editor is
refused every write on a radar it is not listed on, and allowed once the owner adds it."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from session import Api, Deployment

CSV = "title,sector,ring\nE2E row,{sector},{ring}\n"


@pytest.fixture
def other_editor(deployment: Deployment) -> Api:
    user = deployment.user("editor2")
    return Api(deployment.api_url, token=lambda: deployment.sign_in(user)["id"])


@pytest.fixture
def owned(api: dict[str, Api]) -> Iterator[dict[str, Any]]:
    slug = f"e2e-acl-{uuid.uuid4().hex[:8]}"
    status, radar = api["editor"].call("POST", "/api/radars", {"slug": slug, "name": "E2E access"})
    assert status == 201, radar
    yield radar
    api["admin"].call("DELETE", f"/api/radars/{slug}")


def _writes(slug: str, view: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    schema, radar = view["schema"], view["radar"]
    sector, ring = schema["sectors"][0]["name"], schema["rings"][0]["name"]
    version = radar["current_version"]
    return [
        ("PUT", f"/api/radars/{slug}/entries", {"entries": [], "expected_version": version}),
        ("POST", f"/api/radars/{slug}/import", {"format": "csv", "text": CSV.format(sector=sector, ring=ring),
                                                 "expected_version": version}),
        ("POST", f"/api/radars/{slug}/rollback", {"version": 1, "expected_version": version}),
        ("PUT", f"/api/radars/{slug}/schema", {"schema": schema, "expected_version": version}),
        ("PUT", f"/api/radars/{slug}/access", {"editors": [], "version_lock": radar["version_lock"]}),
        ("DELETE", f"/api/radars/{slug}", {}),
    ]


def test_an_unlisted_editor_cannot_write_and_a_listed_one_can(
    api: dict[str, Api], other_editor: Api, owned: dict[str, Any], deployment: Deployment
) -> None:
    slug = owned["slug"]
    view = api["editor"].call("GET", f"/api/radars/{slug}")[1]
    for method, path, body in _writes(slug, view):
        status, payload = other_editor.call(method, path, body or None)
        assert status == 403, (method, path, payload)
    # The owner adds the second editor; it may now import (a write that does not need the owner).
    email = deployment.user("editor2").email
    status, radar = api["editor"].call(
        "PUT", f"/api/radars/{slug}/access",
        {"editors": [email], "version_lock": view["radar"]["version_lock"]},
    )
    assert status == 200 and email in radar["editors"], radar
    view = other_editor.call("GET", f"/api/radars/{slug}")[1]
    sector, ring = view["schema"]["sectors"][0]["name"], view["schema"]["rings"][0]["name"]
    status, imported = other_editor.call(
        "POST", f"/api/radars/{slug}/import",
        {"format": "csv", "text": CSV.format(sector=sector, ring=ring),
         "expected_version": view["radar"]["current_version"]},
    )
    assert status == 200 and imported["entry_count"] == 1, imported
    # Owner-only actions stay the owner's: an editor cannot change who else edits, or delete it.
    status, _ = other_editor.call(
        "PUT", f"/api/radars/{slug}/access", {"editors": [], "version_lock": imported["version_lock"]}
    )
    assert status == 403
    assert other_editor.call("DELETE", f"/api/radars/{slug}")[0] == 403
