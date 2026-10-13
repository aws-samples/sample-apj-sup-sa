"""Consent + retention API (T012) and the functional hard-delete path (T041).

  PUT    /me/consent      -> set consent_store_materials + retention; purge materials on revoke (FR-220)
  DELETE /me              -> hard-delete the account's PII: RDS rows + S3 resume object (FR-219)
  DELETE /sessions/{id}   -> hard-delete one session: turns + blueprint (+ consented S3 audio) (FR-219)

Everything here is off the response_gap clock. Consent GATES personalization: without consent the
server stores no raw materials and PUT /me/resume returns 409 (setup-api.md). Auth: Cognito JWT.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Header, HTTPException

from .. import db
from ..auth_cognito import AuthError, validate_token
from ..resume_store import delete_resume

router = APIRouter(prefix="/api", tags=["consent"])


async def _require_user(authorization: str | None) -> str:
    try:
        return await validate_token(authorization)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.put("/me/consent")
async def put_consent(
    authorization: str | None = Header(default=None),
    consent_store_materials: bool = Body(..., embed=True),
    retention_days: int = Body(30, embed=True),
) -> dict:
    user_sub = await _require_user(authorization)
    if retention_days < 1 or retention_days > 3650:
        raise HTTPException(status_code=422, detail="retention_days out of range (1..3650)")
    # On revoke, the raw S3 resume object must also go (FR-220: no stored material survives without
    # consent). Capture the uri BEFORE set_consent NULLs it, and delete S3 BEFORE the row update —
    # the same privacy-safe ordering as the other deletion paths (a failure here leaves the uri in
    # place so the revoke is re-runnable, never an untracked orphan object).
    if not consent_store_materials:
        user = await db.get_user_by_sub(user_sub)
        delete_resume(user.get("resume_uri") if user else None)
    view = await db.set_consent(user_sub, consent_store_materials, retention_days)
    at = view.get("consent_recording_at")
    return {
        "consent_store_materials": view["consent_recording"],
        "retention_days": view["retention_days"],
        "consent_recording_at": at.isoformat() if at else None,
    }


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, authorization: str | None = Header(default=None)) -> dict:
    user_sub = await _require_user(authorization)
    result = await db.delete_session_cascade(session_id, user_sub)
    if result is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True, "deleted": result}


@router.delete("/me")
async def delete_me(authorization: str | None = Header(default=None)) -> dict:
    user_sub = await _require_user(authorization)
    user = await db.get_user_by_sub(user_sub)
    s3_objects = delete_resume(user.get("resume_uri") if user else None)
    deleted = await db.delete_user_cascade(user_sub)
    deleted["s3_objects"] = s3_objects
    return {"ok": True, "deleted": deleted}
