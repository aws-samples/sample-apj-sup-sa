"""Export a personalized run_session.py JSON into the grounding-eval transcript shape (T046).

The SC-001 driver (harness/run_session.py --personalized --live) measures latency and records each
coach turn's full `reply_text` + structural facts, but it is not shaped for the blind-review evals.
This converter rewrites one such run into the transcript JSON that harness/grounding_eval.py consumes
for Check B (SC-002 grounding) and Check D (SC-004 difficulty):

    {session_id, resume_facts, job_title, job_description,
     turns: [{turn_index, speaker:"coach", transcript, is_followup, archetype_id}]}

Crucially for Check B, the `resume_facts` + job here are reconstructed from the SAME synthetic persona
constants the live prompt was grounded in (run_session._PERSONA_*), so the blind reviewer judges the
coach's questions against exactly the confirmed facts the coach was given — never against a different
set. The grounding is synthetic and carries NO PII (Constitution III). The fixed scripted opener is
prepended as a coach turn so the eval's opener-skip accounting is honest.

Run:
    python -m harness.export_transcript runs/g2_sc001_personalized.json --out runs/g2_grounding_input.json
"""

from __future__ import annotations

import argparse
import json
import os

from harness.run_session import (
    _PERSONA_JOB_REQUIREMENTS,
    _PERSONA_JOB_TITLE,
    _PERSONA_RESUME_HIGHLIGHTS,
)


def _persona_resume_facts() -> dict:
    """Reconstruct the confirmed-facts dict from the synthetic persona highlights.

    Each highlight becomes an experience-detail line so grounding_eval._resume_fact_lines emits the
    exact text the coach prompt was grounded in (no distortion, no added facts)."""
    return {
        "name": None,
        "summary": _PERSONA_RESUME_HIGHLIGHTS[0],
        "skills": [],
        "experience": [{"title": "", "organization": None, "duration": None,
                        "highlights": list(_PERSONA_RESUME_HIGHLIGHTS)}],
        "education": [],
    }


def _persona_job_description() -> str:
    return _PERSONA_JOB_TITLE + "\nKey requirements:\n" + "\n".join(
        f"- {r}" for r in _PERSONA_JOB_REQUIREMENTS
    )


def export(run: dict) -> dict:
    """Convert one personalized run into the grounding-eval transcript shape."""
    from src.reply.interface import OPENING_QUESTION

    turns: list[dict] = [
        {"turn_index": -1, "speaker": "coach", "transcript": OPENING_QUESTION,
         "is_followup": False, "archetype_id": None}
    ]
    for t in run.get("turns") or []:
        text = (t.get("reply_text") or t.get("reply_preview") or "").strip()
        turns.append({
            "turn_index": t.get("turn_index"),
            "speaker": "coach",
            "transcript": text,
            "is_followup": bool(t.get("is_followup")),
            "archetype_id": t.get("archetype_id"),
        })
    return {
        "session_id": run.get("tag") or "g2-personalized-export",
        "difficulty": run.get("difficulty"),
        "resume_facts": _persona_resume_facts(),
        "job_title": _PERSONA_JOB_TITLE,
        "job_description": _persona_job_description(),
        "turns": turns,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Export a personalized run into a blind-review transcript")
    p.add_argument("run", help="a harness/run_session.py --personalized run JSON")
    p.add_argument("--out", required=True, help="path to write the transcript JSON")
    args = p.parse_args(argv)
    with open(args.run) as f:
        run = json.load(f)
    transcript = export(run)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(transcript, f, indent=2)
    print(f"wrote {args.out} ({len(transcript['turns'])} coach turns, "
          f"difficulty={transcript.get('difficulty')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
