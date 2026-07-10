"""Job-description import API — scrape a posting URL into editable {job_title, job_description}.

  POST /me/job/scrape  -> fetch a job-posting URL, extract its text, return facts to prefill the SPA

A convenience that mirrors the resume parse-back: the student pastes a link instead of the full
description. Auth: Cognito bearer JWT. Runs in the setup window — OFF the response_gap clock.

Scope (Constitution III): NOTHING is persisted here. The scraped text is returned to the SPA only;
the job description persists later at session-create, under consent. The URL and page text are never
logged. SSRF hardening lives in jd_scrape (scheme allow-list + non-public-address rejection).
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ..auth_cognito import AuthError, validate_token
from ..jd_scrape import JobScrapeError, scrape_job_description

router = APIRouter(prefix="/api/me/job", tags=["job"])


async def _require_user(authorization: str | None) -> str:
    try:
        return await validate_token(authorization)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


class ScrapeRequest(BaseModel):
    url: str


@router.post("/scrape")
async def scrape_job(
    body: ScrapeRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    # Auth only: scraping persists nothing, so it does not require the consent gate (unlike resume
    # upload). The student still reviews/edits the result before it is used to create a session.
    await _require_user(authorization)
    try:
        result = scrape_job_description(body.url)
    except JobScrapeError as exc:
        # 422: a friendly, safe message telling the student to paste the description instead.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "job_title": result.job_title,
        "job_description": result.job_description,
        "scrape_status": result.scrape_status,
    }
