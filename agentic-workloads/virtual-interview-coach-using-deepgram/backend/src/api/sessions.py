"""Session lifecycle API (T015, T024, T043, T045).

Implements contracts/session-api.md:
  POST /sessions                -> create + media bootstrap (T015)
  POST /sessions/{id}/end       -> explicit end, idempotent (T043)
  GET  /sessions/{id}           -> status + partial transcript (T045)
  GET  /sessions/{id}/latency   -> per-turn rows + gate verdict (T024)

Auth: Cognito bearer JWT on every route. 18+ accounts only.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Header, HTTPException
from pydantic import BaseModel

from .. import audio_playback, db
from ..auth_cognito import AuthError, validate_token
from ..config import settings
from ..prep import blueprint as prep_blueprint
from ..queue import enqueue_report
from ..verdict import compute_verdict
from ..voice_token import build_ice_servers, mint_voice_token

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

_DIFFICULTIES = ("easy", "moderate", "difficult")

# Interview duration -> number of MAIN questions, at ~90s per question (incl. follow-ups). The chosen
# duration is the authoritative length control: the blueprint composes exactly this many questions and
# the worker wraps up when the plan is consumed. Unknown/absent duration falls back to 10 min.
# 3 min is the "quick test drive" tier (F008 US5): a complete miniature interview — first-class
# everywhere sessions appear (list, transcript, playback, guidance) — for demos and smoke checks.
_DURATION_TO_QUESTIONS: dict[int, int] = {3: 2, 5: 3, 10: 6, 15: 9, 30: 16, 45: 24}
_DEFAULT_DURATION_MIN = 10
_DEFAULT_QUESTIONS = _DURATION_TO_QUESTIONS[_DEFAULT_DURATION_MIN]


def _resume_summary(facts: dict | None) -> str:
    """Compact one-paragraph resume summary for grounding generated questions (Generative Mode).

    Built from the confirmed resume facts (name/summary/skills/recent experience). Kept short — it is
    only context for prep-window generation, not the live path."""
    if not facts:
        return ""
    parts: list[str] = []
    if facts.get("summary"):
        parts.append(str(facts["summary"]))
    skills = facts.get("skills") or []
    if skills:
        parts.append("Skills: " + ", ".join(str(s) for s in skills[:20]))
    for exp in (facts.get("experience") or [])[:3]:
        title = exp.get("title") or ""
        org = exp.get("organization") or ""
        hi = "; ".join(str(h) for h in (exp.get("highlights") or [])[:2])
        line = f"{title} at {org}".strip(" at ")
        if hi:
            line += f" — {hi}"
        if line:
            parts.append(line)
    return "\n".join(parts)[:4000]


def _effective_duration(duration_minutes: int | None) -> int:
    """Resolve the requested duration to a supported tier (nearest), or the default when absent. This
    is the length recorded on the session and used by the worker to bound the live interview."""
    if duration_minutes is None:
        return _DEFAULT_DURATION_MIN
    if duration_minutes in _DURATION_TO_QUESTIONS:
        return duration_minutes
    return min(_DURATION_TO_QUESTIONS, key=lambda m: abs(m - duration_minutes))


def _questions_for_duration(duration_minutes: int | None) -> int:
    """Map the requested duration to a main-question count (nearest supported tier, ~90s each)."""
    return _DURATION_TO_QUESTIONS[_effective_duration(duration_minutes)]


async def _require_user(authorization: str | None) -> str:
    try:
        return await validate_token(authorization)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


class CreateSessionRequest(BaseModel):
    # All optional so the G1 generic session (no body) still works; when job scope is present this
    # is a personalized F002 session and consent + a confirmed resume are required (contracts/setup-api.md).
    job_title: str | None = None
    job_description: str | None = None
    difficulty: str | None = None
    # Chosen interview length in minutes (3/5/10/15/30/45; 3 = quick test drive). Drives the
    # question count; absent -> 10 min.
    duration_minutes: int | None = None
    # F006 (G6): per-session recording choice. When False the worker stores no audio for this session
    # (consent-gated — FR-001/FR-002). Defaults True for backward compatibility with existing clients.
    record_audio: bool = True


@router.post("")
async def create_session(
    authorization: str | None = Header(default=None),
    body: CreateSessionRequest | None = Body(default=None),
) -> dict:
    user_sub = await _require_user(authorization)
    provider = settings.reply_provider
    req = body or CreateSessionRequest()

    personalized = bool(req.job_title or req.job_description)
    if not personalized:
        # G1-compatible generic session: no job scope, no blueprint.
        session_id = await db.create_session(user_sub, provider)
        return {
            "session_id": session_id,
            "voice_token": mint_voice_token(session_id, user_sub),
            "media_endpoint": settings.media_endpoint,
            "ice_servers": build_ice_servers(),
            "reply_provider": provider,
        }

    # --- personalized session (F002) -----------------------------------------------------
    difficulty = (req.difficulty or "moderate").lower()
    if difficulty not in _DIFFICULTIES:
        raise HTTPException(status_code=422, detail=f"difficulty must be one of {_DIFFICULTIES}")
    if not req.job_description:
        raise HTTPException(status_code=422, detail="job_description is required for a personalized session")

    user = await db.get_user_by_sub(user_sub)
    # Consent gates personalization; a confirmed resume grounds it (FR-204/220) — 409 if missing.
    if user is None or not user.get("consent_recording"):
        raise HTTPException(status_code=409, detail="consent is required for a personalized session")
    if not user.get("resume_confirmed_at") or not user.get("resume_parsed_facts"):
        raise HTTPException(status_code=409, detail="a confirmed resume is required for a personalized session")

    session_id = await db.create_session(
        user_sub,
        provider,
        user_id=user["id"],
        job_title=req.job_title,
        job_description=req.job_description,
        difficulty=difficulty,
        # F006: the per-session recording flag (account consent already verified above). The worker
        # reads this to decide recording once per session; False -> no audio stored this session.
        consent_store_materials=bool(req.record_audio),
        # Stamp the pinned rubric/tier-context version (FR-215 / Principle II). F002 does not score,
        # but the session records WHICH (difficulty, rubric_version) context it ran under so later
        # cross-session comparison never blends tiers into a single always-up score.
        rubric_version=settings.rubric_version,
        # Record the chosen length so the worker can bound the live session to it (duration gating).
        duration_minutes=_effective_duration(req.duration_minutes),
    )

    # Assemble the JD-ranked plan in the prep window, BEFORE returning, so blueprint_ready is true
    # by the time the client may start media (off the gap clock — FR-208 / SC-003). The question
    # count comes from the chosen duration; the blueprint composes that many across the category mix.
    num_questions = _questions_for_duration(req.duration_minutes)
    try:
        plan = await prep_blueprint.assemble_blueprint(
            session_id, req.job_title or "", req.job_description, difficulty,
            num_questions=num_questions,
            resume_summary=_resume_summary(user.get("resume_parsed_facts")),
        )
    except RuntimeError as exc:
        # Neither the vetted bank NOR prep-window generation (Generative Mode, Constitution VII) could
        # produce a plan for this difficulty. Roll back the empty session so it cannot be started
        # without a plan, and surface a 503 honestly.
        await db.delete_session_cascade(session_id, user_sub)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "session_id": session_id,
        "voice_token": mint_voice_token(session_id, user_sub),
        "media_endpoint": settings.media_endpoint,
        "ice_servers": build_ice_servers(),
        "reply_provider": provider,
        "difficulty": difficulty,
        "rubric_version": settings.rubric_version,
        "blueprint_ready": True,
        "domain_coverage_reduced": plan["domain_coverage_reduced"],
    }


@router.post("/{session_id}/end")
async def end_session(session_id: str, authorization: str | None = Header(default=None)) -> dict:
    user_sub = await _require_user(authorization)
    # Owner-scope (anti-IDOR): only the session's owner may end it / trigger its report job. 404 (not
    # 403) for a non-owner so we don't leak which session ids exist.
    if not await db.session_owned_by(session_id, user_sub):
        raise HTTPException(status_code=404, detail="session not found")
    sess = await db.end_session(session_id, end_reason="student_ended")
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    # F003: enqueue the async report job (FR-301). This is the ONLY live-path touch F003 adds — a job
    # row insert + one SQS send — then return immediately. NO scoring runs here (the Report Worker does
    # it off the live path), so SC-001 is not re-opened (SC-003). Enqueue failure is non-fatal to /end.
    await db.enqueue_report_job(session_id)
    enqueue_report(session_id)
    return {
        "session_id": session_id,
        "end_reason": sess["end_reason"],
        "ended_at": sess["ended_at"].isoformat() if sess["ended_at"] else None,
        "turn_count": sess["turn_count"],
        "report_status": "processing",
    }


@router.get("")
async def list_sessions(authorization: str | None = Header(default=None)) -> dict:
    """The owner's past practice sessions, newest first (F008 US1 — the session picker).
    Owner scoping is inherent: the query is keyed by the caller's own sub; there is no foreign id
    to probe. report_status lets the SPA label entries without polling each report."""
    user_sub = await _require_user(authorization)
    rows = await db.list_sessions_for_owner(user_sub)
    return {
        "sessions": [
            {
                "session_id": r["session_id"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "ended_at": r["ended_at"].isoformat() if r["ended_at"] else None,
                "end_reason": r["end_reason"],
                "job_title": r["job_title"],
                "difficulty": r["difficulty"],
                "duration_minutes": r["duration_minutes"],
                "report_status": r["report_status"],
            }
            for r in rows
        ]
    }


@router.get("/{session_id}")
async def read_session(session_id: str, authorization: str | None = Header(default=None)) -> dict:
    user_sub = await _require_user(authorization)
    if not await db.session_owned_by(session_id, user_sub):
        raise HTTPException(status_code=404, detail="session not found")  # anti-IDOR, no existence leak
    sess = await db.get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {
        "session_id": sess["session_id"],
        "end_reason": sess["end_reason"],
        "network_path": sess["network_path"],
        "reply_provider": sess["reply_provider"],
        # Recorded difficulty tier + rubric_version, surfaced unblended (FR-215 / SC-008): a later
        # feature compares within a tier and never collapses tiers into one score.
        "difficulty": sess.get("difficulty"),
        "rubric_version": sess.get("rubric_version"),
        "started_at": sess["started_at"].isoformat() if sess["started_at"] else None,
        "ended_at": sess["ended_at"].isoformat() if sess["ended_at"] else None,
        # turns now carry turn_id + has_audio (F006) so the SPA can offer per-answer playback; the
        # signed URL is minted separately and owner-scoped.
        "turns": sess["turns"],
    }


@router.get("/{session_id}/latency")
async def read_latency(session_id: str, authorization: str | None = Header(default=None)) -> dict:
    user_sub = await _require_user(authorization)
    if not await db.session_owned_by(session_id, user_sub):
        raise HTTPException(status_code=404, detail="session not found")  # anti-IDOR
    data = await db.get_latency(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="session not found")
    gaps = [t["response_gap_ms"] for t in data["turns"]]
    aggregate = compute_verdict(gaps)
    return {
        "session_id": session_id,
        "reply_provider": data["reply_provider"],
        "network_path": data["network_path"],
        "turns": data["turns"],
        "aggregate": aggregate,
    }


@router.get("/{session_id}/report")
async def read_report(session_id: str, authorization: str | None = Header(default=None)) -> dict:
    """Poll the async feedback report (FR-309). Returns {status} while queued/processing/failed and
    {status:'scored', report} once the Report Worker has written it. 404 if no report job exists."""
    user_sub = await _require_user(authorization)
    if not await db.session_owned_by(session_id, user_sub):
        raise HTTPException(status_code=404, detail="no report for this session")  # anti-IDOR
    result = await db.get_report(session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="no report for this session")
    return result


@router.get("/{session_id}/turns/{turn_id}/audio-url")
async def get_turn_audio_url(
    session_id: str, turn_id: str, authorization: str | None = Header(default=None)
) -> dict:
    """Per-answer playback (F006 / G6). Mints a short-lived S3 pre-signed GET URL for the turn's audio,
    ONLY for the authenticated owner of the session (FR-007/008/009). 404 (no existence leak) for a
    non-owner; {"available": false} when the turn has no recording (FR-010). The URL is never logged."""
    user_sub = await _require_user(authorization)
    owned = await db.get_turn_audio_uri_for_owner(session_id, turn_id, user_sub)
    if owned is None:
        raise HTTPException(status_code=404, detail="not found")  # not owner / no such session
    return audio_playback.build_playback(owned["audio_uri"])
