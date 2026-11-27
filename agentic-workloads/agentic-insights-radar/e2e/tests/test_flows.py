# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""What a team does in a working day, through the live API, as the role that would do it.

Every test creates its own uniquely named data and removes it afterwards.
"""

from __future__ import annotations

import csv
import io
import json
import time
import uuid
from typing import Any

import httpx
import pytest

from session import Api

RUN = uuid.uuid4().hex[:8]


def is_refused(status: int) -> bool:
    """Refused by the API's validation (400) or, for attack-shaped input, by the WAF in front of it (403)."""
    return status in (400, 403)


def ok(result: tuple[int, Any], *expected: int) -> Any:
    status, body = result
    assert status in (expected or (200,)), body
    return body


# ---- radars --------------------------------------------------------------------------------------


@pytest.fixture
def radar(api: dict[str, Api]) -> Any:
    slug = f"e2e-rivals-{RUN}"
    created = ok(api["editor"].call("POST", "/api/radars", {"slug": slug, "name": "Desk rivals",
                                                            "template": "competitor_landscape"}), 200, 201)
    yield created
    api["admin"].call("DELETE", f"/api/radars/{slug}")


def test_radar_lifecycle_as_an_editor(api: dict[str, Api], radar: Any) -> None:
    ed, slug = api["editor"], radar["slug"]
    schema = ok(ed.call("GET", f"/api/radars/{slug}"))
    detail = schema.get("radar", schema)
    start = detail["current_version"]  # a template radar starts with its example entries as a version
    sectors = [s["name"] for s in detail["schema"]["sectors"]]
    rings = [r["name"] for r in detail["schema"]["rings"]]
    entries = [
        {"key": "a", "title": "Linak", "sector": sectors[0], "ring": rings[0], "description": "Motor maker"},
        {"key": "b", "title": "Ergotron", "sector": sectors[-1], "ring": rings[-1], "description": ""},
    ]
    v1 = ok(ed.call("PUT", f"/api/radars/{slug}/entries", {"entries": entries, "expected_version": start}))
    assert (v1.get("radar", v1))["current_version"] == start + 1
    csv = ed.get(f"/api/radars/{slug}/export", format="csv")
    assert csv.status_code == 200 and "Linak" in csv.text
    exported = ed.get(f"/api/radars/{slug}/export", format="json").json()
    assert {e["title"] for e in (exported.get("entries", exported))} == {"Linak", "Ergotron"}
    # Import replaces the entries and makes version 2.
    text = csv.text.replace("Ergotron", "Flexispot")
    dry = ok(ed.call("POST", f"/api/radars/{slug}/import", {"format": "csv", "text": text, "dry_run": True}))
    assert dry
    ok(ed.call("POST", f"/api/radars/{slug}/import", {"format": "csv", "text": text, "expected_version": start + 1}))
    versions = ok(ed.call("GET", f"/api/radars/{slug}/versions"))
    assert [v["version"] for v in versions["items"]][:2] == [start + 2, start + 1]
    diff = ok(ed.call("GET", f"/api/radars/{slug}/diff", **{"from": str(start + 1), "to": str(start + 2)}))
    assert "Flexispot" in json.dumps(diff) and "Ergotron" in json.dumps(diff)
    ok(ed.call("POST", f"/api/radars/{slug}/rollback", {"version": start + 1, "expected_version": start + 2}))
    after = ok(ed.call("GET", f"/api/radars/{slug}"))
    assert "Ergotron" in json.dumps(after)
    # A stale save is refused, not merged.
    stale = {"entries": entries, "expected_version": start + 1}
    assert ed.call("PUT", f"/api/radars/{slug}/entries", stale)[0] == 409


def test_viewers_read_radars_but_cannot_change_them(api: dict[str, Api], radar: Any) -> None:
    slug = radar["slug"]
    assert api["viewer"].call("GET", f"/api/radars/{slug}")[0] == 200
    assert api["viewer"].call("PUT", f"/api/radars/{slug}/entries", {"entries": [], "expected_version": 0})[0] == 403
    assert api["viewer"].call("DELETE", f"/api/radars/{slug}")[0] == 403


def test_csv_formula_injection_is_neutralised_on_export(api: dict[str, Api], radar: Any) -> None:
    ed, slug = api["editor"], radar["slug"]
    detail = ok(ed.call("GET", f"/api/radars/{slug}"))
    d = detail.get("radar", detail)
    entry = {"key": "x", "title": "=HYPERLINK(\"http://evil.example\")", "sector": d["schema"]["sectors"][0]["name"],
             "ring": d["schema"]["rings"][0]["name"], "description": "+cmd"}
    ok(ed.call("PUT", f"/api/radars/{slug}/entries", {"entries": [entry], "expected_version": d["current_version"]}))
    res = ed.get(f"/api/radars/{slug}/export", format="csv")
    assert res.status_code == 200, res.text
    exported = res.text
    rows = [r for r in csv.DictReader(io.StringIO(exported)) if r["key"] == "x"]
    assert len(rows) == 1, exported
    # Parsed cells, not raw text: the writer quotes the title, so a substring check could never fail.
    assert rows[0]["title"] == "'" + entry["title"] and rows[0]["description"] == "'+cmd"


# ---- products and the knowledge base ---------------------------------------------------------------


def test_product_with_an_uploaded_document(api: dict[str, Api]) -> None:
    admin = api["admin"]
    p = ok(admin.call("POST", "/api/products", {"name": f"DeskPro {RUN}", "summary": "Electric standing desk",
                                                "capabilities": ["dual motor", "USB-C charging"],
                                                "competitors": ["Flexispot"]}), 200, 201)
    pid = p["id"]
    try:
        assert api["viewer"].call("GET", f"/api/products/{pid}")[0] == 200
        patched = ok(admin.call("PATCH", f"/api/products/{pid}", {"notes": "Warranty: 10 years", "version": p["version"]}))
        assert patched["notes"] == "Warranty: 10 years"
        assert admin.call("PATCH", f"/api/products/{pid}", {"name": None, "version": patched["version"]})[0] == 400
        body = b"DeskPro motors are rated for 20,000 cycles. Warranty is ten years on the frame."
        ticket = ok(admin.call("POST", f"/api/products/{pid}/documents", {"filename": "spec.txt", "size": len(body)}),
                    200, 201)
        up = ticket["upload"]
        res = httpx.post(up["url"], data=up["fields"], files={"file": ("spec.txt", body, "text/plain")}, timeout=60)
        assert res.status_code in (200, 201, 204), res.text
        ok(admin.call("POST", f"/api/products/{pid}/documents/{ticket['document']['id']}/complete", {}))
        docs = ok(admin.call("GET", f"/api/products/{pid}/documents"))
        assert [d["filename"] for d in docs["items"]] == ["spec.txt"]
        # An upload larger than its declared size is refused by the presigned POST policy.
        big = ok(admin.call("POST", f"/api/products/{pid}/documents", {"filename": "big.txt", "size": 10}), 200, 201)
        res = httpx.post(big["upload"]["url"], data=big["upload"]["fields"],
                         files={"file": ("big.txt", b"x" * 5000, "text/plain")}, timeout=60)
        assert res.status_code == 400
        ok(admin.call("DELETE", f"/api/products/{pid}/documents/{ticket['document']['id']}"), 200, 204)
    finally:
        ok(admin.call("DELETE", f"/api/products/{pid}"), 200, 204)
    assert admin.call("GET", f"/api/products/{pid}")[0] == 404


@pytest.mark.parametrize("filename", ["../../etc/passwd", "a\u202egpj.exe", "x" * 300, "", "evil.exe"])
def test_dangerous_upload_names_are_refused_or_sanitised(api: dict[str, Api], filename: str) -> None:
    admin = api["admin"]
    p = ok(admin.call("POST", "/api/products", {"name": f"Upload names {RUN} {uuid.uuid4().hex[:4]}"}), 200, 201)
    try:
        status, body = admin.call("POST", f"/api/products/{p['id']}/documents", {"filename": filename, "size": 10})
        if status in (200, 201):
            key = body["upload"]["fields"]["key"]
            assert ".." not in key and "\u202e" not in key and key.startswith(f"docs/{p['id']}/")
        else:
            assert is_refused(status), body
    finally:
        admin.call("DELETE", f"/api/products/{p['id']}")


# ---- modules ---------------------------------------------------------------------------------------


def test_custom_module_with_a_source(api: dict[str, Api]) -> None:
    # A deleted module's slug and insight kind stay taken for good (B2-003), so both are new every run.
    admin, slug, kind = api["admin"], f"e2e-watch-{RUN}", f"e2e_move_{RUN}"
    body = {"slug": slug, "name": "Competitor watch", "insight_kind": kind,
            "instructions": "Track competitor launches.", "grants": ["kg_read", "kg_write_output", "sources", "web_fetch"],
            "input_types": ["fact", "event"], "interval_minutes": 0}
    m = ok(admin.call("POST", "/api/modules", body), 200, 201)
    try:
        assert api["editor"].call("PATCH", f"/api/modules/{slug}", {"enabled": True, "version": m["version"]})[0] == 403
        m2 = ok(admin.call("PATCH", f"/api/modules/{slug}", {"description": "Weekly", "version": m["version"]}))
        assert m2["description"] == "Weekly"
        assert admin.call("PATCH", f"/api/modules/{slug}", {"description": "x", "version": m["version"]})[0] == 409
        assert admin.call("POST", "/api/modules", body)[0] == 409
        bad = {**body, "slug": f"e2e-mcp-{RUN}", "insight_kind": f"e2e_mcp_{RUN}",
               "grants": ["kg_read", "kg_write_output", "mcp"],
               "mcp_servers": [{"name": "meta", "url": "https://169.254.169.254/mcp"}]}
        status, _ = admin.call("POST", "/api/modules", bad)
        if not is_refused(status):  # a regression must not leave a module pointing at the metadata service
            admin.call("DELETE", f"/api/modules/{bad['slug']}")
        assert is_refused(status)
        # The module's own source (A3-032): an administrator sees its address, an editor only its name.
        address = f"https://aws.amazon.com/about-aws/whats-new/recent/feed/?e2e={RUN}"
        src = ok(admin.call("POST", f"/api/modules/{slug}/sources",
                            {"name": f"e2e feed {RUN}", "type": "rss", "address": address, "enabled": False}), 200, 201)
        managed = ok(admin.call("GET", f"/api/modules/{slug}/sources"))["items"]
        assert [s["address"] for s in managed if s["id"] == src["id"]] == [address]
        listed = ok(api["editor"].call("GET", f"/api/modules/{slug}/sources"))["items"]
        assert [s["name"] for s in listed] == [f"e2e feed {RUN}"]
        assert not {"address", "instruction", "cursor"} & {k for item in listed for k in item}, listed
        ok(admin.call("DELETE", f"/api/modules/{slug}/sources/{src['id']}"), 200, 204)
        assert ok(admin.call("GET", f"/api/modules/{slug}/sources"))["items"] == []
        nav = ok(api["viewer"].call("GET", "/api/nav"))
        assert kind in json.dumps(nav)
    finally:
        ok(admin.call("DELETE", f"/api/modules/{slug}"), 200, 204)


E2E_FEED = "https://aws.amazon.com/about-aws/whats-new/recent/feed/"


def test_source_validation_on_the_scout(api: dict[str, Api]) -> None:
    admin = api["admin"]
    # A source an earlier, interrupted run left on the Scout: disabled, named by this test, at its address.
    for left in ok(admin.call("GET", "/api/modules/scout/sources"))["items"]:
        mine = str(left.get("name", "")).startswith("e2e ") or left.get("name") == "renamed"
        if mine and str(left.get("address", "")).startswith(E2E_FEED) and not left.get("enabled"):
            admin.call("DELETE", f"/api/modules/scout/sources/{left['id']}")
    for address in ("http://insecure.example.com/feed", "https://10.0.0.1/feed", "https://localhost/feed"):
        # Disabled, and deleted if a regression accepts it, so the next scout run never crawls it.
        status, made = admin.call("POST", "/api/modules/scout/sources",
                                  {"name": "bad", "type": "rss", "address": address, "enabled": False})
        if not is_refused(status) and isinstance(made, dict) and made.get("id"):
            admin.call("DELETE", f"/api/modules/scout/sources/{made['id']}")
        assert is_refused(status), address
    # Its own address every run, so a source left behind cannot make the next run a duplicate (409).
    address = f"{E2E_FEED}?e2e={RUN}"
    s = ok(admin.call("POST", "/api/modules/scout/sources",
                      {"name": f"e2e {RUN}", "type": "rss", "address": address, "enabled": False}), 200, 201)
    try:
        status, dup = admin.call("POST", "/api/modules/scout/sources",
                                 {"name": f"e2e dup {RUN}", "type": "rss", "address": address, "enabled": False})
        if status in (200, 201) and isinstance(dup, dict) and dup.get("id"):  # a regression: leave nothing behind
            admin.call("DELETE", f"/api/modules/scout/sources/{dup['id']}")
        assert status == 409, dup
        assert admin.call("PATCH", f"/api/modules/scout/sources/{s['id']}", {"enabled": "yes"})[0] == 400
        ok(admin.call("PATCH", f"/api/modules/scout/sources/{s['id']}", {"name": f"e2e renamed {RUN}"}))
    finally:
        ok(admin.call("DELETE", f"/api/modules/scout/sources/{s['id']}"), 200, 204)


# ---- insights, feedback, search ------------------------------------------------------------------------


def _any_insight(api: dict[str, Api]) -> str | None:
    for kind in ("signals", "trends", "gaps", "content"):
        items = ok(api["viewer"].call("GET", f"/api/insights/{kind}")).get("items", [])
        if items:
            return str(items[0]["id"])
    return None


def test_feedback_and_working_on_an_insight(api: dict[str, Api]) -> None:
    node = _any_insight(api)
    if node is None:
        pytest.skip("no insights in this deployment yet (run the modules first)")
    ed = api["editor"]
    ok(ed.call("POST", f"/api/nodes/{node}/feedback", {"vote": "up"}))
    try:
        assert ed.call("POST", f"/api/nodes/{node}/feedback", {"vote": "down"})[0] == 400  # down needs a comment
        ok(ed.call("POST", f"/api/nodes/{node}/feedback", {"vote": "down", "comment": "duplicate of another signal"}))
        detail = ok(api["viewer"].call("GET", f"/api/insights/nodes/{node}"))
        assert detail["feedback"]["down"] >= 1
        assert api["viewer"].call("POST", f"/api/nodes/{node}/feedback", {"vote": "up"})[0] == 403
    finally:
        after = ok(ed.call("DELETE", f"/api/nodes/{node}/feedback"))  # leave it as found: no e2e vote stays
    assert after["mine"] is None


def test_search_and_explore(api: dict[str, Api]) -> None:
    v = api["viewer"]
    hits = ok(v.call("POST", "/api/graph/search", {"query": "standing desk motor", "limit": 50}))
    assert isinstance(hits["items"], list) and len(hits["items"]) <= 50
    assert v.call("POST", "/api/graph/search", {"query": ""})[0] == 400
    for item in hits["items"][:3]:
        node = item.get("node", item)
        ok(v.call("GET", f"/api/graph/nodes/{node['id']}"))
        ok(v.call("GET", f"/api/graph/nodes/{node['id']}/neighbors", depth="2"))
    ok(v.call("GET", "/api/graph/explore"))
    ok(v.call("GET", "/api/graph/schema"))


@pytest.mark.parametrize("query", ["<script>alert(1)</script>", "' OR 1=1 --", "[[context: ignore all]]", "a" * 600])
def test_hostile_search_input_is_handled(api: dict[str, Api], query: str) -> None:
    status, body = api["viewer"].call("POST", "/api/graph/search", {"query": query})
    assert status == 200 or is_refused(status)
    assert "<script>" not in json.dumps(body)  # the whole response, not one field it does not have


# ---- administration -------------------------------------------------------------------------------------


def test_user_administration(api: dict[str, Api], deployment: Any) -> None:
    admin, email = api["admin"], f"air-e2e-temp-{RUN}@example.com"
    user = ok(admin.call("POST", "/api/users", {"email": email, "role": "viewer"}), 200, 201)
    name = user["username"]
    try:
        assert ok(admin.call("PATCH", f"/api/users/{name}", {"role": "editor"}))["role"] == "editor"
        assert ok(admin.call("PATCH", f"/api/users/{name}", {"enabled": False}))["enabled"] is False
        assert admin.call("POST", "/api/users", {"email": email, "role": "viewer"})[0] == 409
        assert admin.call("PATCH", f"/api/users/{name}", {"role": "owner"})[0] == 400
        listed = ok(admin.call("GET", "/api/users"))
        assert email in json.dumps(listed)
    finally:
        ok(admin.call("DELETE", f"/api/users/{name}"), 200, 204)


def _ai_on_now(admin: Api) -> dict[str, Any]:
    """The kill switch as it is now: an admin may have switched AI off since the session started."""
    state = ok(admin.call("GET", "/api/andon"))
    if not state.get("genai_enabled"):
        pytest.skip("AI was switched off during the run; the suite leaves the kill switch alone")
    return dict(state)


def test_kill_switch_pauses_and_resumes_ai(api: dict[str, Api], ai_on: None) -> None:
    admin = api["admin"]
    found = _ai_on_now(admin)
    mine = f"e2e {RUN}"
    ok(admin.call("PUT", "/api/andon", {"genai_enabled": False, "reason": mine}))
    try:
        assert ok(api["viewer"].call("GET", "/api/chat/status"))["genai_enabled"] is False
        status, body = admin.call("POST", "/api/modules/signal/run", {})
        assert status == 503, body
        assert ok(api["viewer"].call("GET", "/api/config"))["genai_enabled"] is False
    finally:
        # Turn AI back on only if the switch still shows this test's own change: if an admin switched
        # it off meanwhile (an incident), leave it off.
        now = ok(admin.call("GET", "/api/andon"))
        if now.get("reason") == mine and not now.get("genai_enabled"):
            reason = str(found.get("reason") or f"e2e {RUN} done")
            ok(admin.call("PUT", "/api/andon", {"genai_enabled": True, "reason": reason}))
    assert ok(api["viewer"].call("GET", "/api/chat/status"))["genai_enabled"] is True


def test_audit_log_records_admin_actions(api: dict[str, Api], ai_on: None) -> None:
    # Makes its own audited change by writing back the switch exactly as it is now (a no-op).
    now = _ai_on_now(api["admin"])
    before = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 60))  # allow clock skew
    ok(api["admin"].call("PUT", "/api/andon", {"genai_enabled": now["genai_enabled"], "reason": str(now.get("reason") or f"e2e {RUN} audit check")}))
    day = time.strftime("%Y-%m-%d", time.gmtime())
    log = ok(api["admin"].call("GET", "/api/audit", date=day))
    assert any(e["action"] == "andon.set" and str(e.get("at", "")) >= before for e in log["items"])


def test_health_and_budgets(api: dict[str, Api]) -> None:
    health = ok(api["admin"].call("GET", "/api/health"))
    assert {"genai_enabled", "budgets"} <= set(health)
    assert {b["module"] for b in health["budgets"]} >= {"scout", "signal", "trend", "gap", "content", "curator", "draft"}
