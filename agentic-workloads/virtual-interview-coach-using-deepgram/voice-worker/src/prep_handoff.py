"""Session-start handoff: minimize the prep plan + confirmed resume facts into the live payload (T018).

The backend assembles the JD-ranked plan and stores the CONFIRMED resume facts in RDS during the
prep window (off the response_gap clock). At session start the worker reads them
(persistence.load_interview_plan) and this module MINIMIZES them into exactly what the live turn
needs (FR-204 / FR-218):

  - resume_highlights: a short list of salient CONFIRMED facts (never the raw resume; bounded).
  - job_scope: the role title + a few key requirements derived deterministically from the JD.
  - the ordered plan rows + opening archetype -> the in-memory BlueprintQueue.
  - the difficulty profile levers -> persona tuning (US3).

Raw PII (full resume text, full JD blob) stays in RDS/S3; only these minimized, derived facts enter
the live TurnContext, and nothing here is logged (FR-218 / Principle III). No scores (Principle II).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .reply.interface import DifficultyProfile, JobScope

# Bounds keep the live prompt small and the handoff cheap; the live turn only needs salient facts.
_MAX_HIGHLIGHTS = 8
_MAX_SKILLS = 8
_MAX_EXPERIENCE = 3
_MAX_REQS = 6

# Requirement-like cues used to prefer the JD lines that actually state expectations. Deterministic
# (NO LLM) — JD parsing is off the gap clock but kept cheap and dependency-free regardless.
_REQ_KEYWORDS = (
    "experience", "proficient", "knowledge", "ability", "familiar", "skill", "required",
    "must", "strong", "understanding", "degree", "years", "expertise", "background",
    "responsible", "lead", "design", "build", "develop",
)


@dataclass
class SessionPlan:
    """The minimized, derived grounding payload handed to the live loop at session start.

    Everything here is derived from the CONFIRMED resume facts + the JD-ranked plan already in RDS;
    it carries no raw resume text and no scores. `plan_rows` (ordered) seeds the BlueprintQueue.
    """

    resume_highlights: list[str] = field(default_factory=list)
    job_scope: JobScope | None = None
    target_competencies: list[str] = field(default_factory=list)
    difficulty_profile: DifficultyProfile | None = None
    plan_rows: list[dict] = field(default_factory=list)
    opening_archetype_id: str | None = None
    # F006 (G6): whether this session may record audio (= voice_session.consent_store_materials).
    # Decided ONCE here from the session's consent snapshot; the worker reads it to gate recording for
    # the whole session (research R2 / FR-002). No consent -> the worker never buffers or uploads audio.
    record_audio: bool = False
    # The student's chosen interview length (minutes). The worker bounds the live session to it so a
    # slow funnel can't overrun the chosen duration; None -> derive the budget from the question count.
    duration_minutes: int | None = None


def _trim(text: str, limit: int) -> str:
    """Collapse whitespace and cap length (keeps a highlight to a single short line)."""
    t = " ".join(str(text).split())
    return t if len(t) <= limit else t[: limit - 3].rstrip() + "..."


def minimize_resume_highlights(parsed_facts: dict | None) -> list[str]:
    """Derive a bounded list of salient CONFIRMED facts for grounding (FR-204).

    Maps the confirmed parsed-facts shape (summary / skills / experience / education) into short
    single-line highlights. Never includes raw resume text and never the full structure; bounded to
    `_MAX_HIGHLIGHTS`. Returns [] when there are no confirmed facts.
    """
    if not parsed_facts:
        return []

    highlights: list[str] = []

    summary = parsed_facts.get("summary")
    if summary:
        highlights.append(_trim(summary, 240))

    skills = [str(s).strip() for s in (parsed_facts.get("skills") or []) if str(s).strip()]
    if skills:
        highlights.append("Key skills: " + ", ".join(skills[:_MAX_SKILLS]))

    for exp in (parsed_facts.get("experience") or [])[:_MAX_EXPERIENCE]:
        if not isinstance(exp, dict):
            continue
        title = (exp.get("title") or "").strip()
        org = (exp.get("organization") or "").strip()
        dur = (exp.get("duration") or "").strip()
        if not title and not org:
            continue
        line = title
        if org:
            line += f" at {org}" if line else org
        if dur:
            line += f" ({dur})"
        exp_highlights = exp.get("highlights") or []
        if exp_highlights:
            line += " - " + _trim(exp_highlights[0], 160)
        highlights.append(line.strip())

    education = parsed_facts.get("education") or []
    if education and isinstance(education[0], dict):
        e = education[0]
        qual = (e.get("qualification") or "").strip()
        inst = (e.get("institution") or "").strip()
        if qual or inst:
            line = qual
            if inst:
                line += f", {inst}" if line else inst
            highlights.append(line.strip())

    return [h for h in highlights if h][:_MAX_HIGHLIGHTS]


def _extract_requirements(job_description: str) -> list[str]:
    """Pull a few key-requirement lines from the JD deterministically (no LLM).

    Splits on newlines and sentence boundaries, strips bullet markers, keeps reasonable-length
    lines, and prefers ones that read like requirements. Bounded to `_MAX_REQS`.
    """
    if not job_description.strip():
        return []

    raw_lines: list[str] = []
    for line in job_description.splitlines():
        # A JD pasted as one blob still yields useful units when split on sentence punctuation.
        raw_lines.extend(re.split(r"(?<=[.;])\s+", line))

    candidates: list[str] = []
    for line in raw_lines:
        s = line.strip().lstrip("-*•·●◦").strip()
        if 10 <= len(s) <= 200:
            candidates.append(s)

    preferred = [c for c in candidates if any(k in c.lower() for k in _REQ_KEYWORDS)]
    chosen = preferred or candidates

    seen: set[str] = set()
    out: list[str] = []
    for c in chosen:
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= _MAX_REQS:
            break
    return out


def derive_job_scope(job_title: str | None, job_description: str | None) -> JobScope:
    """The target role as the live turn sees it (FR-203): title + a few key requirements."""
    title = (job_title or "").strip() or "the target role"
    return JobScope(title=title, key_requirements=_extract_requirements(job_description or ""))


def build_session_plan(raw: dict) -> SessionPlan:
    """Turn persistence.load_interview_plan's raw read into the minimized live SessionPlan."""
    profile = None
    p = raw.get("difficulty_profile")
    if p is not None:
        profile = DifficultyProfile(
            level=p["level"],
            probing_intensity=int(p["probing_intensity"]),
            curveball_rate=float(p["curveball_rate"]),
            warmth=int(p["warmth"]),
            hint_policy=p["hint_policy"],
            domain_depth=int(p["domain_depth"]),
        )
    return SessionPlan(
        resume_highlights=minimize_resume_highlights(raw.get("resume_facts")),
        job_scope=derive_job_scope(raw.get("job_title"), raw.get("job_description")),
        target_competencies=list(raw.get("target_competencies") or []),
        difficulty_profile=profile,
        plan_rows=list(raw.get("plan_rows") or []),
        opening_archetype_id=raw.get("opening_archetype_id"),
        record_audio=bool(raw.get("consent_store_materials")),
        duration_minutes=raw.get("duration_minutes"),
    )
