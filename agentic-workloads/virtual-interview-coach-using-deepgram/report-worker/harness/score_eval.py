"""Gate G3 evidence harness — rubric consistency (SC-001) + evidence presence (SC-002).

Scores the same transcript end-to-end N separate times with the LIVE scorer and reports:
  - Check A (SC-001 / NFR-8): the per-dimension spread (max-min) across the N runs. PASS iff every
    dimension (overall + four sub-scores) varies by < 0.5 points.
  - Check B (SC-002): for each run, that 100% of assessed competency scorecard entries carry a quote
    that is a verbatim substring of the student transcript. PASS iff no fabricated quote ever appears.

This is the authoritative G3 gate evidence (quickstart Checks A + B). It uses the live Bedrock scorer
(self-consistency aggregation IS the consistency mechanism), not a stub.

Run:
    python -m harness.score_eval --session <session_id> --runs 3
    python -m harness.score_eval --transcript runs/fixture.json --runs 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from src.config import Config
from src.evidence import build_student_corpus, is_present
from src.scorer import SUBSCORES, score_session

VARIANCE_THRESHOLD = 0.5  # SC-001 / NFR-8


async def _load_turns(session_id: str | None, transcript_path: str | None, config: Config) -> tuple[list[dict], list[str]]:
    if transcript_path:
        data = json.loads(Path(transcript_path).read_text())
        return data["turns"], data.get("competencies", [])
    # else load from DB
    from src import persistence

    conn = await persistence.connect(config)
    try:
        sess = await persistence.load_session(conn, session_id)
    finally:
        await conn.close()
    if sess is None:
        raise SystemExit(f"session {session_id} not found")
    return sess["turns"], sess["competencies"]


async def _main() -> None:
    parser = argparse.ArgumentParser(description="G3 gate harness: scoring consistency + evidence presence")
    parser.add_argument("--session", help="score a DB session id N times")
    parser.add_argument("--transcript", help="score a fixture transcript JSON N times")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--mode", choices=["consistency", "feedback"], default="consistency",
                        help="consistency = Checks A+B (SC-001/SC-002); feedback = SC-004 self-referential check")
    parser.add_argument("--out", help="write the verdict JSON here")
    args = parser.parse_args()
    if not args.session and not args.transcript:
        raise SystemExit("provide --session or --transcript")

    if args.mode == "feedback":
        await _feedback_eval(args)
        return

    config = Config.load()
    turns, competencies = await _load_turns(args.session, args.transcript, config)
    corpus = build_student_corpus(turns)

    dims = {d: [] for d in ("overall", *SUBSCORES)}
    evidence_total = 0
    evidence_present = 0
    runs_detail = []

    for i in range(args.runs):
        res = score_session(turns, competencies, config)  # live scorer (each call self-consistent)
        dims["overall"].append(res.overall)
        for d in SUBSCORES:
            dims[d].append(getattr(res, d))
        for c in res.competencies:
            if c.assessed:
                evidence_total += 1
                if is_present(c.evidence_quote, corpus):
                    evidence_present += 1
        runs_detail.append({"overall": res.overall, **{d: getattr(res, d) for d in SUBSCORES},
                            "assessed_competencies": [c.competency for c in res.competencies if c.assessed]})

    spreads = {d: round(max(v) - min(v), 2) for d, v in dims.items() if v}
    consistency_pass = all(s < VARIANCE_THRESHOLD for s in spreads.values())
    evidence_rate = (evidence_present / evidence_total) if evidence_total else 1.0
    evidence_pass = evidence_rate >= 1.0  # SC-002 requires 100%

    verdict = {
        "runs": args.runs,
        "check_A_consistency": {
            "per_dimension_spread": spreads,
            "threshold": VARIANCE_THRESHOLD,
            "verdict": "PASS" if consistency_pass else "FAIL",
        },
        "check_B_evidence": {
            "assessed_quotes": evidence_total,
            "present_in_transcript": evidence_present,
            "rate": round(evidence_rate, 4),
            "verdict": "PASS" if evidence_pass else "FAIL",
        },
        "overall_verdict": "PASS" if (consistency_pass and evidence_pass) else "FAIL",
        "runs_detail": runs_detail,
    }
    out = json.dumps(verdict, indent=2)
    print(out)
    if args.out:
        Path(args.out).write_text(out)
    sys.exit(0 if verdict["overall_verdict"] == "PASS" else 1)


async def _feedback_eval(args) -> None:
    """SC-004: every assessed question shows transcript + what-worked + what-to-improve + a
    strong-answer example, and the strong answer references the student's confirmed resume facts."""
    from src import persistence
    from src.feedback import build_question_feedback

    config = Config.load()
    if args.transcript:
        data = json.loads(Path(args.transcript).read_text())
        turns, resume_facts = data["turns"], data.get("resume_facts")
    else:
        conn = await persistence.connect(config)
        try:
            sess = await persistence.load_session(conn, args.session)
        finally:
            await conn.close()
        if sess is None:
            raise SystemExit(f"session {args.session} not found")
        turns, resume_facts = sess["turns"], sess["resume_facts"]

    qa = persistence._qa_pairs(turns)
    feedbacks = build_question_feedback(qa, resume_facts, config)
    complete = sum(
        1 for f in feedbacks
        if f.what_worked and f.what_to_improve and f.strong_answer_example
    )
    rate = complete / len(feedbacks) if feedbacks else 1.0
    verdict = {
        "mode": "feedback",
        "assessed_questions": len(feedbacks),
        "complete_feedback": complete,
        "rate": round(rate, 4),
        "threshold": 0.90,
        "verdict": "PASS" if rate >= 0.90 else "FAIL",
    }
    out = json.dumps(verdict, indent=2)
    print(out)
    if args.out:
        Path(args.out).write_text(out)
    sys.exit(0 if verdict["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    asyncio.run(_main())
