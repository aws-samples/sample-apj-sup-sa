"""Tests for the evidence-loop backend half: request-error logging + the client-events beacon.

Offline: a minimal FastAPI app exercises the middleware (no DB, no Cognito); the beacon's auth
seam is monkeypatched. Asserts the structured lines appear (method + route + status only — no
bodies/tokens) and that the beacon enforces its event-name allowlist.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src import observability
from src.observability import install_error_logging, router as observability_router

pytestmark = []


def _make_app() -> FastAPI:
    app = FastAPI()
    install_error_logging(app)
    app.include_router(observability_router)

    @app.get("/api/ok")
    async def ok():
        return {"ok": True}

    @app.get("/api/needs-auth")
    async def needs_auth():
        raise HTTPException(status_code=401, detail="missing token")

    @app.get("/api/boom")
    async def boom():
        raise RuntimeError("kaboom secret-detail")

    return app


def test_401_logged_with_route_and_status_only(caplog):
    client = TestClient(_make_app())
    with caplog.at_level(logging.WARNING, logger="backend"):
        resp = client.get("/api/needs-auth")
    assert resp.status_code == 401
    line = next(r.message for r in caplog.records if "request error" in r.message)
    assert "GET /api/needs-auth -> 401" in line


def test_unhandled_exception_logged_as_500_without_internals(caplog):
    client = TestClient(_make_app(), raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="backend"):
        resp = client.get("/api/boom")
    assert resp.status_code == 500
    assert resp.json() == {"detail": "internal error"}
    line = next(r.message for r in caplog.records if "unhandled error" in r.message)
    assert "GET /api/boom -> 500 (RuntimeError)" in line
    # The exception MESSAGE (which could carry user input) never reaches the log or the client.
    assert "kaboom" not in line and "secret-detail" not in resp.text


def test_success_and_404_not_logged(caplog):
    client = TestClient(_make_app())
    with caplog.at_level(logging.WARNING, logger="backend"):
        assert client.get("/api/ok").status_code == 200
        assert client.get("/api/nope").status_code == 404  # anti-IDOR 404s stay quiet
    assert not [r for r in caplog.records if "request error" in r.message]


# --- client-events beacon ----------------------------------------------------------------


@pytest.fixture
def authed(monkeypatch):
    async def fake_validate(_authorization):
        return "sub-1"

    monkeypatch.setattr(observability, "validate_token", fake_validate)


def test_beacon_logs_allowlisted_event(authed, caplog):
    client = TestClient(_make_app())
    with caplog.at_level(logging.INFO, logger="backend"):
        resp = client.post(
            "/api/client-events",
            json={"event": "mic_denied"},
            headers={"Authorization": "Bearer t"},
        )
    assert resp.status_code == 200
    assert any("client event: mic_denied" in r.message for r in caplog.records)


def test_beacon_rejects_unknown_event_and_never_logs_it(authed, caplog):
    client = TestClient(_make_app())
    with caplog.at_level(logging.INFO, logger="backend"):
        resp = client.post(
            "/api/client-events",
            json={"event": "<script>alert(1)</script>"},
            headers={"Authorization": "Bearer t"},
        )
    assert resp.status_code == 422
    assert not any("script" in r.message for r in caplog.records)


def test_beacon_requires_auth(monkeypatch, caplog):
    from src.auth_cognito import AuthError

    async def deny(_authorization):
        raise AuthError("no token")

    monkeypatch.setattr(observability, "validate_token", deny)
    client = TestClient(_make_app())
    resp = client.post("/api/client-events", json={"event": "mic_denied"})
    assert resp.status_code == 401
