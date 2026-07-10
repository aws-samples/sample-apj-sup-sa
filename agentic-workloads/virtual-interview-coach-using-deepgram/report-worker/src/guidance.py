"""Cross-session coaching guidance (F008 / Gate G5, US4).

After a session is scored, this module synthesizes the user's WHOLE scored history into a
professional coach's prose: recurring strengths, recurring improvement areas, an honest trend
note, and 2-3 prioritized next actions. One Bedrock converse call (the same client/model family
the scorer uses), grounded EXCLUSIVELY in the user's own report-derived material (no raw
transcripts, no other users' data — Constitution III / SC-005), validated to a strict JSON
shape, and UPSERTed as the user's one current guidance row.

Honesty rules baked into the prompt (Constitution II): no new composite score is invented; if
the source sessions span multiple rubric versions the trend wording must say so rather than
blending; a single-session history gets useful single-session advice plus "trends unlock with
more practice" — never a fabricated trajectory.

Containment: a guidance failure is logged and swallowed by the caller (consume.py) — it never
affects the scoring result or the consume loop, and the previous guidance row stays in place
(the dashboard keeps showing it with its generated_at — FR-012).
"""

from __future__ import annotations

import json
import logging

from .config import Config

log = logging.getLogger("report_worker")

_GUIDANCE_SYSTEM = (
    "You are a seasoned, encouraging professional interview coach reviewing ALL of one "
    "candidate's practice-interview reports, oldest to newest. Write coach-style guidance "
    "addressed directly to them (second person), grounded ONLY in the material provided — "
    "never invent specifics they did not say, and reference real examples from their sessions "
    "where possible.\n\n"
    "Honesty rules (mandatory): do NOT invent any overall composite score or numeric rating of "
    "your own; numbers may only be cited from the reports themselves. If the reports span "
    "different rubric versions, say plainly that scores across them are not directly comparable. "
    "If there is only ONE session, give useful single-session guidance and note that trends "
    "unlock with more practice — do not fabricate a trajectory.\n\n"
    "Return STRICT JSON (no markdown fence, no commentary) with exactly these keys:\n"
    '{"strengths": [2-4 short prose observations of what RECURS as strong],\n'
    ' "improvement_areas": [2-4 short prose observations of what KEEPS recurring as weak],\n'
    ' "trend_note": "2-3 sentences on direction over time, honest about limits",\n'
    ' "next_actions": [exactly 2 or 3 concrete, prioritized practice actions]}\n'
    "Keep every item specific, kind, and actionable — the tone of a coach the candidate trusts."
)


def _coerce_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    return json.loads(text)


def _material_digest(material: list[dict]) -> str:
    """Compact, model-readable digest of the scored history (oldest -> newest)."""
    lines: list[str] = []
    for i, m in enumerate(material, 1):
        lines.append(
            f"--- Session {i} ({m.get('created_at') or 'unknown date'}; role: "
            f"{m.get('job_title') or 'general practice'}; difficulty: {m.get('difficulty')}; "
            f"rubric: {m.get('rubric_version')}) ---"
        )
        lines.append(
            "Scores (0-10): overall %s, content %s, structure %s, communication %s, confidence %s"
            % (m.get("overall"), m.get("score_content"), m.get("score_structure"),
               m.get("score_communication"), m.get("score_confidence"))
        )
        if m.get("summary_strengths"):
            lines.append("Strengths noted: " + "; ".join(map(str, m["summary_strengths"])))
        if m.get("summary_improvements"):
            lines.append("Improvements noted: " + "; ".join(map(str, m["summary_improvements"])))
        for c in m.get("competency_scorecard") or []:
            if isinstance(c, dict) and c.get("assessed"):
                quote = f' (they said: "{c.get("evidence_quote")}")' if c.get("evidence_quote") else ""
                lines.append(f"Competency {c.get('competency')}: {c.get('score_1_5')}/5{quote}")
    return "\n".join(lines)


def _generate_once(material: list[dict], config: Config) -> dict:
    import boto3

    client = boto3.client("bedrock-runtime", region_name=config.aws_region)
    resp = client.converse(
        modelId=config.bedrock_model_id,
        system=[{"text": _GUIDANCE_SYSTEM}],
        messages=[{"role": "user", "content": [{"text": _material_digest(material)[:24000]}]}],
        inferenceConfig={"maxTokens": 1500, "temperature": 0.3},
    )
    return _coerce_json(resp["output"]["message"]["content"][0]["text"])


def _validated(raw: dict) -> dict | None:
    """Clamp/validate the generation output to the contract; None if unusable."""
    try:
        strengths = [str(s) for s in raw["strengths"] if str(s).strip()]
        improvements = [str(s) for s in raw["improvement_areas"] if str(s).strip()]
        trend = str(raw["trend_note"]).strip()
        actions = [str(s) for s in raw["next_actions"] if str(s).strip()]
    except (KeyError, TypeError):
        return None
    if not strengths or not improvements or not trend or len(actions) < 2:
        return None
    return {
        "strengths": strengths[:4],
        "improvement_areas": improvements[:4],
        "trend_note": trend,
        "next_actions": actions[:3],  # contract: exactly 2-3 (clamped)
    }


def build_guidance(material: list[dict], config: Config, *, generate_fn=None) -> dict | None:
    """Generate + validate guidance from the scored material. ONE retry on malformed output;
    None when the material is empty or both attempts are unusable (caller keeps the previous
    guidance — never write garbage). `generate_fn` is injectable for tests."""
    if not material:
        return None
    fn = generate_fn or _generate_once
    for attempt in (1, 2):
        try:
            payload = _validated(fn(material, config))
        except Exception as exc:  # noqa: BLE001 - malformed JSON / model error -> retry once
            log.warning("guidance generation attempt %d failed (%s)", attempt, type(exc).__name__)
            payload = None
        if payload is not None:
            payload["sessions_analyzed"] = len(material)
            payload["rubric_versions"] = sorted(
                {str(m.get("rubric_version")) for m in material if m.get("rubric_version")}
            )
            payload["model_id"] = config.bedrock_model_id
            return payload
    return None


async def refresh_guidance(conn, user_sub: str, config: Config, *, generate_fn=None) -> bool:
    """Load material -> generate -> upsert. Returns True when a new row was written. Failures
    return False (logged by the caller's containment wrapper); the previous guidance survives."""
    from . import persistence

    material = await persistence.load_scored_material_for_user(conn, user_sub)
    payload = build_guidance(material, config, generate_fn=generate_fn)
    if payload is None:
        return False
    await persistence.upsert_guidance(conn, user_sub, payload)
    log.info(
        "coaching guidance refreshed: %d session(s) analyzed", payload["sessions_analyzed"]
    )
    return True
