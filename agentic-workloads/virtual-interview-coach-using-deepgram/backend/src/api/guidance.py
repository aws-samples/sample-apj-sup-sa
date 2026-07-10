"""Coaching guidance API (F008 / Gate G5, US4).

  GET /api/me/guidance -> the caller's current cross-session coaching guidance, or
                          {"available": false} when none exists yet.

The row is keyed by the caller's OWN user_sub — there is no foreign id to probe (SC-006).
Generation happens asynchronously in the report-worker after each scoring; this endpoint only
reads. Off the live path entirely.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from .. import db
from ..auth_cognito import AuthError, validate_token

router = APIRouter(prefix="/api", tags=["guidance"])


async def _require_user(authorization: str | None) -> str:
    try:
        return await validate_token(authorization)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/me/guidance")
async def get_guidance(authorization: str | None = Header(default=None)) -> dict:
    user_sub = await _require_user(authorization)
    row = await db.get_guidance(user_sub)
    if row is None:
        return {"available": False}
    return {
        "available": True,
        "generated_at": row["generated_at"].isoformat() if row["generated_at"] else None,
        "sessions_analyzed": row["sessions_analyzed"],
        "strengths": row["strengths"],
        "improvement_areas": row["improvement_areas"],
        "trend_note": row["trend_note"],
        "next_actions": row["next_actions"],
    }
