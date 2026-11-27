# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The web app in Chromium, signed in through the hosted UI with TOTP, as each role."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

from browser import console_errors, sign_in
from session import Deployment

PAGES = ["/", "/insights/signals", "/insights/trends", "/insights/gaps", "/insights/content", "/radars", "/graph",
         "/products", "/modules"]


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture(scope="module", params=["viewer", "editor", "admin"])
def signed_in(request: Any, browser: Browser, deployment: Deployment) -> Iterator[tuple[str, Page, list[str]]]:
    role = request.param
    deployment.sign_in(deployment.user(role))  # enrols TOTP on first use
    context = browser.new_context()
    page = context.new_page()
    errors = console_errors(page)
    sign_in(page, deployment, deployment.user(role))
    yield role, page, errors
    context.close()


@pytest.mark.browser
@pytest.mark.parametrize("path", PAGES)
def test_every_page_loads_without_errors(signed_in: tuple[str, Page, list[str]], deployment: Deployment,
                                         path: str) -> None:
    role, page, errors = signed_in
    errors.clear()
    page.goto(f"{deployment.app_url}{path}")
    page.wait_for_load_state("networkidle")
    expect(page.get_by_text("This page does not exist.")).to_have_count(0)
    assert errors == [], f"{role} {path}: {errors}"


@pytest.mark.browser
def test_administration_is_only_offered_to_admins(signed_in: tuple[str, Page, list[str]],
                                                  deployment: Deployment) -> None:
    role, page, _ = signed_in
    page.goto(f"{deployment.app_url}/")
    page.wait_for_load_state("networkidle")
    link = page.get_by_role("link", name="Administration")
    if role == "admin":
        expect(link).to_be_visible()
        page.goto(f"{deployment.app_url}/admin")
        expect(page.get_by_role("tab", name="Users")).to_be_visible()
    else:
        expect(link).to_have_count(0)


@pytest.mark.browser
def test_viewers_get_no_edit_controls(signed_in: tuple[str, Page, list[str]], deployment: Deployment) -> None:
    role, page, _ = signed_in
    page.goto(f"{deployment.app_url}/radars")
    page.wait_for_load_state("networkidle")
    create = page.get_by_role("button", name="Create radar")
    if role == "viewer":
        expect(create).to_have_count(0)
    else:
        expect(create).to_be_visible()


@pytest.mark.browser
def test_unknown_route_shows_not_found(signed_in: tuple[str, Page, list[str]], deployment: Deployment) -> None:
    _, page, _ = signed_in
    page.goto(f"{deployment.app_url}/no/such/page")
    expect(page.get_by_text("This page does not exist.")).to_be_visible()


@pytest.fixture(scope="module")
def fixture_radar(deployment: Deployment) -> Iterator[dict[str, Any]]:
    """A radar made for this run (an e2e-* slug): the render test never walks transient radars."""
    import uuid

    from session import Api

    editor = Api(deployment.api_url, token=lambda: deployment.sign_in(deployment.user("editor"))["id"])
    slug = f"e2e-render-{uuid.uuid4().hex[:8]}"
    status, radar = editor.call("POST", "/api/radars", {"slug": slug, "name": "E2E render"})
    assert status == 201, radar
    view = editor.call("GET", f"/api/radars/{slug}")[1]
    sector, ring = view["schema"]["sectors"][0]["name"], view["schema"]["rings"][0]["name"]
    rows = "".join(f"E2E entry {i},{sector},{ring}\n" for i in range(1, 4))
    status, saved = editor.call("POST", f"/api/radars/{slug}/import", {
        "format": "csv", "text": f"title,sector,ring\n{rows}", "expected_version": view["radar"]["current_version"]})
    assert status == 200 and saved["entry_count"] == 3, saved
    yield {"slug": slug, "api": editor, "count": 3}
    Api(deployment.api_url, token=lambda: deployment.sign_in(deployment.user("admin"))["id"]).call(
        "DELETE", f"/api/radars/{slug}")


@pytest.mark.browser
def test_a_radar_renders_its_entries_and_round_trips(signed_in: tuple[str, Page, list[str]], deployment: Deployment,
                                                     fixture_radar: dict[str, Any]) -> None:
    """CTR 96dd8ef6: pages were only loaded, never checked against what the API holds."""
    role, page, errors = signed_in
    slug, editor = fixture_radar["slug"], fixture_radar["api"]
    errors.clear()
    page.goto(f"{deployment.app_url}/radars/{slug}")
    chart = page.get_by_role("group", name="E2E render radar")
    expect(chart).to_be_visible()
    expect(chart.get_by_role("button")).to_have_count(fixture_radar["count"])
    expect(page.get_by_role("region", name="Legend entries").get_by_text("E2E entry 2")).to_be_visible()
    # CSV and JSON exports import back unchanged: a dry run accepts them and removes nothing.
    version = editor.call("GET", f"/api/radars/{slug}")[1]["radar"]["current_version"]
    for fmt in ("csv", "json"):
        status, text = editor.call("GET", f"/api/radars/{slug}/export", format=fmt)
        assert status == 200, text
        body = text if isinstance(text, str) else json.dumps(text)
        status, dry = editor.call("POST", f"/api/radars/{slug}/import",
                                  {"format": fmt, "text": body, "dry_run": True})
        assert status == 200 and dry["accepted"] and dry["removed"] == 0 and dry["rows"] == 3, (fmt, dry)
    # The version history lists the import, and an unknown slug is a not-found page, not a crash.
    page.goto(f"{deployment.app_url}/radars/{slug}?tab=history")
    expect(page.get_by_text(f"Version {version}").first).to_be_visible()
    page.goto(f"{deployment.app_url}/radars/e2e-no-such-radar-{slug[-8:]}")
    expect(page.get_by_text("not found", exact=False).first).to_be_visible()
    assert [e for e in errors if "404" not in e] == [], f"{role}: {errors}"
