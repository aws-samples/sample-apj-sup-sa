"""Resume upload + parse-back API (T013/T014) — contracts/setup-api.md.

  PUT  /me/resume          -> upload raw file to S3 (SSE-KMS), off-gap-clock parse, return facts (FR-201)
  POST /me/resume/confirm  -> confirmed/corrected facts become authoritative for grounding (FR-204)
  GET  /me/resume          -> return stored confirmed facts for reuse-with-confirm (FR-202)

Consent GATES persistence: PUT /me/resume returns 409 without prior consent (FR-220). Everything
here runs in the setup window — off the response_gap clock (R4). Raw resume bytes and parsed facts
are PII: they go to S3/RDS under consent and are NEVER logged.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from pydantic import BaseModel

from .. import db
from ..auth_cognito import AuthError, validate_token
from ..resume_parse import parse_resume
from ..resume_store import put_resume

router = APIRouter(prefix="/api/me/resume", tags=["resume"])

# Guardrail for the upload size (off-gap-clock, but bound memory). Resumes are small documents.
_MAX_RESUME_BYTES = 5 * 1024 * 1024


async def _require_user(authorization: str | None) -> str:
    try:
        return await validate_token(authorization)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


class ConfirmRequest(BaseModel):
    parsed_facts: dict
    manual_entry: bool = False


@router.put("")
async def upload_resume(
    authorization: str | None = Header(default=None),
    file: UploadFile = File(...),
) -> dict:
    user_sub = await _require_user(authorization)

    # Consent gates persistence (FR-220): no consent => no stored raw materials => 409.
    user = await db.get_user_by_sub(user_sub)
    if user is None or not user.get("consent_recording"):
        raise HTTPException(
            status_code=409,
            detail="a personalized session requires consent to store your materials",
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=422, detail="empty file")
    if len(file_bytes) > _MAX_RESUME_BYTES:
        raise HTTPException(status_code=413, detail="resume exceeds 5 MB limit")

    filename = file.filename or "resume.pdf"
    content_type = file.content_type or "application/octet-stream"

    # Store the raw file first (sole durable home alongside RDS facts), then parse off the gap clock.
    resume_uri = put_resume(user["id"], file_bytes, filename, content_type)
    result = parse_resume(file_bytes, filename)
    await db.set_resume(user_sub, resume_uri, result.parsed_facts)

    return {
        "resume_uri": resume_uri,
        "parsed_facts": result.parsed_facts,
        "parse_status": result.parse_status,
        "confidence": result.confidence,
    }


@router.post("/confirm")
async def confirm_resume(
    body: ConfirmRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_sub = await _require_user(authorization)

    # Confirmation also requires consent: the confirmed facts are stored materials (FR-220).
    user = await db.get_user_by_sub(user_sub)
    if user is None or not user.get("consent_recording"):
        raise HTTPException(
            status_code=409,
            detail="consent is required before storing confirmed resume facts",
        )

    confirmed_at = await db.confirm_resume(user_sub, body.parsed_facts)
    return {"resume_confirmed_at": confirmed_at.isoformat()}


@router.get("")
async def get_resume(authorization: str | None = Header(default=None)) -> dict:
    user_sub = await _require_user(authorization)
    user = await db.get_user_by_sub(user_sub)
    facts = user.get("resume_parsed_facts") if user else None
    confirmed_at = user.get("resume_confirmed_at") if user else None
    if not facts:
        raise HTTPException(status_code=404, detail="no stored resume")
    return {
        "parsed_facts": facts,
        "resume_confirmed_at": confirmed_at.isoformat() if confirmed_at else None,
        "still_accurate_prompt": True,
    }
