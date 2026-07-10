"""Request-error logging + the client-events beacon (the evidence loop's backend half).

Two gaps this closes:
  1. Auth failures (401/403) and server errors (5xx) only appeared in uvicorn's access log —
     no structured line to filter/alert on, so an error spike or an enumeration attempt was
     invisible. The middleware logs method + route + status + exception type. NEVER bodies,
     query strings, headers, or tokens (Constitution III: no PII in logs).
  2. Client-side failures (mic denied, WebRTC connect failure, mid-session drop, render error)
     died in the browser console — the server had no trace. POST /api/client-events lets the
     SPA report an event NAME from a fixed allowlist; it is logged (a structured line, counts
     only) and dropped — no DB row, no payload, nothing user-supplied except the enum.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .auth_cognito import AuthError, validate_token

log = logging.getLogger("backend")

# The only event names the beacon accepts — anything else is rejected (422), so the log stream
# can never carry user-controlled strings.
CLIENT_EVENTS = frozenset(
    {
        "connect_failed",  # WebRTC offer/ICE never reached "connected"
        "mic_denied",  # getUserMedia NotAllowedError
        "mic_unavailable",  # no usable input device
        "session_dropped",  # ICE failed/closed mid-interview (unexpected end)
        "render_error",  # ErrorBoundary caught a render crash
        "report_load_failed",  # report polling gave up with an error
        "playback_failed",  # 'Play my answer' audio element errored (F008 US3)
    }
)

router = APIRouter(prefix="/api", tags=["observability"])


@router.post("/client-events")
async def client_event(
    request: Request,
    authorization: str | None = Header(default=None),
    event: str = Body(..., embed=True),
) -> dict:
    """Accept one client-side event name (allowlisted) and log it. Fire-and-forget by design:
    authenticated (so it cannot be spammed anonymously), no storage, no free-text."""
    try:
        await validate_token(authorization)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if event not in CLIENT_EVENTS:
        raise HTTPException(status_code=422, detail="unknown event")
    log.info("client event: %s", event)
    return {"ok": True}


def install_error_logging(app) -> None:
    """HTTP middleware: one structured WARNING per 401/403/5xx response (and ERROR for unhandled
    exceptions), with method, route, and status only. 404s are deliberately NOT logged — the
    anti-IDOR design returns 404 for non-owned resources, and logging those would just mirror
    probe traffic; a 5xx/auth-failure spike is the actionable signal."""

    @app.middleware("http")
    async def _log_request_errors(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001 - log, then return a clean 500 (no internals)
            log.error(
                "unhandled error: %s %s -> 500 (%s)",
                request.method,
                request.url.path,
                type(exc).__name__,
            )
            return JSONResponse(status_code=500, content={"detail": "internal error"})
        if response.status_code in (401, 403) or response.status_code >= 500:
            log.warning(
                "request error: %s %s -> %d",
                request.method,
                request.url.path,
                response.status_code,
            )
        return response
