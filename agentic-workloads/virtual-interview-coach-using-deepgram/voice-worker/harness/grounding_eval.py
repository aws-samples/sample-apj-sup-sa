"""Grounding (SC-002) blind-review eval (T023).

Quickstart Check B: a BLIND reviewer reads a personalized session's transcript and marks, for
each opening/main (non-follow-up) coach question, the confirmed resume fact and/or job requirement
it verifiably references. The session PASSES SC-002 iff **>= 90%** of those questions are grounded.

    python -m harness.grounding_eval --session <session_id> --mode grounding

This is an OFFLINE eval: it runs entirely off the `response_gap` clock (after the session, against
RDS), so the reviewer LLM call never touches the live latency budget. It is the SC-002 counterpart
of harness/aggregate.py (SC-001) and produces a verdict JSON of the same spirit.

Honesty (Constitution II):
  - The reviewer judges grounding ONLY against the candidate's CONFIRMED resume facts + the actual
    job posting — never against what the student happened to say. It is "blind" in that it is not
    told which fact a question was *meant* to reference; it independently decides if the question
    references any of them.
  - The fixed scripted greeting ("tell me about yourself") is the loop's deterministic opener, not a
    generated/bank question, so it carries no grounding obligation; it is skipped and the skip is
    recorded (it is not silently dropped from a passing rate).
  - Follow-up turns (`is_followup = TRUE`) are excluded by definition — SC-002 is about the
    opening/main questions; follow-up containment is SC-005 (`--mode containment`, T029).
  - `--heuristic` swaps the LLM reviewer for a deterministic keyword-overlap check so the pipeline
    is exercisable without Bedrock credentials. Those numbers are clearly labeled and MUST NOT be
    used as the gate decision.

Modes:
  - `grounding`   (SC-002, T023): the blind-review grounding rate above.
  - `containment` (SC-005, T029): a DETERMINISTIC structural check — every follow-up turn
    (`is_followup = TRUE`) must carry the SAME `archetype_id` as the most recent preceding main
    (non-follow-up) coach question, i.e. a probe never drifts into a new competency. No LLM is
    involved (it reads the persisted structural facts directly); it is a hard invariant, so ANY
    violation fails the check. Cross-session comparability depends on it (Principle V). It runs
    against RDS (`--session`) or any per-turn JSON carrying `archetype_id`/`is_followup` — including
    `harness/run_session.py --personalized` output, so T028's run feeds T029 directly.
  - `difficulty`  (SC-004, T033): a paired blind review. Given two runs of the SAME resume + job at
    different tiers (one Easy, one Difficult), the reviewer must identify which transcript is the
    Difficult one. PASS iff it is correct in >= 90% of paired trials. Each `--pair easy,difficult`
    names two runs (a `run_session.py --tag` value -> runs/<tag>.json, or an explicit JSON path);
    Easy is given first as the ground truth, but the two are presented to the reviewer as A/B with
    the order ALTERNATED per pair (no RNG) so position cannot be exploited. `--heuristic` swaps the
    LLM for a deterministic tier-signal score (DRY, not a gate decision). This runs entirely offline.

Run:
    python -m harness.grounding_eval --session <session_id> --mode grounding
    python -m harness.grounding_eval --transcript runs/transcript.json --heuristic   # offline/dry
    python -m harness.grounding_eval --transcript runs/personalized.json --mode containment  # SC-005
    python -m harness.grounding_eval --pair easy,difficult --mode difficulty --heuristic       # SC-004
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass

# NOTE: heavy imports (asyncpg, boto3, src.*) are done lazily inside the functions that need them so
# the DRY/heuristic + transcript-file path runs without DB drivers or AWS credentials installed.

PASS_THRESHOLD = 0.90  # SC-002: >= 90% of opening/main questions verifiably grounded.


@dataclass
class Question:
    """One opening/main (non-follow-up) coach question under review."""

    turn_index: int
    text: str


@dataclass
class GroundingVerdict:
    """The reviewer's per-question judgement."""

    grounded: bool
    resume_fact: str | None
    job_requirement: str | None
    rationale: str


# --- Grounding material (what a question may be grounded in) -----------------------------------


def _resume_fact_lines(parsed_facts: dict | None) -> list[str]:
    """Flatten the CONFIRMED parsed facts into readable, individually-checkable lines.

    Unbounded (this is an offline eval — fairness beats brevity here): a question grounded in a fact
    outside the live prompt's top-N highlights still counts as grounded against the confirmed set.
    """
    if not parsed_facts:
        return []

    lines: list[str] = []
    name = (parsed_facts.get("name") or "").strip()
    if name:
        lines.append(f"Name: {name}")
    summary = (parsed_facts.get("summary") or "").strip()
    if summary:
        lines.append(f"Summary: {summary}")

    for s in parsed_facts.get("skills") or []:
        s = str(s).strip()
        if s:
            lines.append(f"Skill: {s}")

    for exp in parsed_facts.get("experience") or []:
        if not isinstance(exp, dict):
            continue
        title = (exp.get("title") or "").strip()
        org = (exp.get("organization") or "").strip()
        dur = (exp.get("duration") or "").strip()
        head = title
        if org:
            head += f" at {org}" if head else org
        if dur:
            head += f" ({dur})"
        head = head.strip()
        if head:
            lines.append(f"Experience: {head}")
        for h in exp.get("highlights") or []:
            h = str(h).strip()
            if h:
                lines.append(f"Experience detail: {h}")

    for ed in parsed_facts.get("education") or []:
        if not isinstance(ed, dict):
            continue
        qual = (ed.get("qualification") or "").strip()
        inst = (ed.get("institution") or "").strip()
        year = str(ed.get("year") or "").strip()
        parts = [p for p in (qual, inst, year) if p]
        if parts:
            lines.append("Education: " + ", ".join(parts))

    return lines


def _select_main_questions(turns: list[dict]) -> tuple[list[Question], int]:
    """Pick the opening/main (non-follow-up) coach questions, skipping the scripted greeting.

    Returns (questions, skipped_opening_count). A turn is a candidate iff it is a coach turn that is
    not a follow-up (`is_followup` falsy — defaults to FALSE on a US1 session, so all coach questions
    are mains until US2 marks follow-ups). The verbatim fixed opener is skipped and counted.
    """
    from src.reply.interface import OPENING_QUESTION

    opener = OPENING_QUESTION.strip()
    questions: list[Question] = []
    skipped = 0
    for t in turns:
        if (t.get("speaker") or "") != "coach":
            continue
        if t.get("is_followup"):
            continue
        text = (t.get("transcript") or "").strip()
        if not text:
            continue
        if text == opener:
            skipped += 1
            continue
        questions.append(Question(turn_index=int(t.get("turn_index", -1)), text=text))
    return questions, skipped


# --- Reviewers -----------------------------------------------------------------------------------

_STOPWORDS = frozenset(
    """a an and are as at be but by can could did do does for from had has have how i if in into is
    it its me my of on or our so tell that the their them then there these they this to us was we
    were what when where which who why will with would you your about would like just been being
    over more most much very some any all one two also into out up down""".split()
)


def _salient_tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 4 and w not in _STOPWORDS}


def _heuristic_review(
    question: str, resume_lines: list[str], job_title: str, job_description: str
) -> GroundingVerdict:
    """Deterministic keyword-overlap grounding check (DRY — NOT a gate decision).

    Marks a question grounded if it shares a salient token with any confirmed resume line or with the
    job posting. Crude on purpose: it exercises the pipeline offline; the real verdict uses the LLM.
    """
    q_tokens = _salient_tokens(question)
    for line in resume_lines:
        overlap = q_tokens & _salient_tokens(line)
        if overlap:
            return GroundingVerdict(True, line, None, f"shared terms: {', '.join(sorted(overlap))}")
    job_tokens = _salient_tokens(job_title + " " + job_description)
    overlap = q_tokens & job_tokens
    if overlap:
        return GroundingVerdict(
            True, None, job_title or "job posting",
            f"shared job terms: {', '.join(sorted(overlap)[:6])}",
        )
    return GroundingVerdict(False, None, None, "no salient overlap with resume or job posting")


_REVIEWER_SYSTEM = (
    "You are a strict, impartial reviewer auditing whether a practice-interview question is GROUNDED "
    "in a specific candidate or role. You are given (1) the candidate's CONFIRMED resume facts and "
    "(2) the target job posting. Decide whether the coach's question verifiably references at least "
    "one specific confirmed resume fact AND/OR one specific requirement of the job posting.\n"
    "Rules: judge ONLY against the resume facts and job posting provided — never assume facts not "
    "listed. A generic question that could be asked of anyone (no reference to a listed fact or a "
    "stated job requirement) is NOT grounded. Quote the exact fact or requirement you matched.\n"
    "Respond with ONLY a JSON object, no prose:\n"
    '{"grounded": <true|false>, "resume_fact": <string or null>, '
    '"job_requirement": <string or null>, "rationale": <short string>}'
)


def _build_reviewer_user_msg(
    question: str, resume_lines: list[str], job_title: str, job_description: str
) -> str:
    facts = "\n".join(f"- {ln}" for ln in resume_lines) or "- (no confirmed resume facts on file)"
    jd = (job_description or "").strip()
    if len(jd) > 4000:  # offline, but keep the prompt sane on a pasted mega-JD
        jd = jd[:4000] + " ...[truncated]"
    return (
        f"CONFIRMED RESUME FACTS:\n{facts}\n\n"
        f"TARGET JOB TITLE: {job_title or '(unspecified)'}\n"
        f"TARGET JOB POSTING:\n{jd or '(none provided)'}\n\n"
        f"COACH QUESTION TO AUDIT:\n\"{question}\"\n\n"
        "Is this question grounded in a specific confirmed resume fact and/or a stated job "
        "requirement? Reply with the JSON object only."
    )


def _parse_reviewer_json(text: str) -> GroundingVerdict:
    """Extract the reviewer's JSON verdict defensively (model may wrap it in prose)."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return GroundingVerdict(False, None, None, "reviewer returned no parseable JSON")
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return GroundingVerdict(False, None, None, "reviewer JSON did not parse")
    return GroundingVerdict(
        grounded=bool(obj.get("grounded")),
        resume_fact=(obj.get("resume_fact") or None),
        job_requirement=(obj.get("job_requirement") or None),
        rationale=str(obj.get("rationale") or ""),
    )


def _bedrock_review(
    client, model_id: str, question: str, resume_lines: list[str], job_title: str, job_description: str
) -> GroundingVerdict:
    """One blind grounding judgement via Bedrock Converse (temperature 0 for a stable verdict)."""
    resp = client.converse(
        modelId=model_id,
        system=[{"text": _REVIEWER_SYSTEM}],
        messages=[
            {
                "role": "user",
                "content": [
                    {"text": _build_reviewer_user_msg(question, resume_lines, job_title, job_description)}
                ],
            }
        ],
        inferenceConfig={"maxTokens": 400, "temperature": 0},
    )
    text = resp["output"]["message"]["content"][0]["text"]
    return _parse_reviewer_json(text)


# --- Data sources --------------------------------------------------------------------------------


async def _load_session_data(database_url: str, session_id: str) -> dict:
    """Read the session's transcript + grounding context (confirmed facts + job) from RDS.

    Reuses Persistence.load_interview_plan for the confirmed facts/job (it returns None for a generic
    G1 session — which has no blueprint and therefore nothing to ground, so we error clearly).
    """
    import asyncpg

    from src.persistence import Persistence

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    try:
        plan = await Persistence(pool).load_interview_plan(session_id)
        if plan is None:
            raise SystemExit(
                f"session {session_id} has no interview blueprint — it is a generic G1 session with "
                "nothing to ground (grounding eval applies to personalized F002 sessions)."
            )
        rows = await pool.fetch(
            """
            SELECT turn_index, speaker, transcript, is_followup, archetype_id
              FROM conversation_turn WHERE session_id = $1 ORDER BY turn_index
            """,
            uuid.UUID(session_id),
        )
    finally:
        await pool.close()

    return {
        "session_id": session_id,
        "resume_facts": plan.get("resume_facts"),
        "job_title": plan.get("job_title"),
        "job_description": plan.get("job_description"),
        "turns": [dict(r) for r in rows],
    }


def _load_transcript_file(path: str) -> dict:
    """Load a pre-exported transcript JSON (offline path — no RDS needed).

    Shape: {session_id, resume_facts, job_title, job_description, turns:[{turn_index, speaker,
    transcript, is_followup}]}. Lets the eval be exercised end-to-end without a live database.
    """
    with open(path) as f:
        data = json.load(f)
    data.setdefault("turns", [])
    return data


# --- Grounding run -------------------------------------------------------------------------------


async def _run_grounding(data: dict, heuristic: bool) -> dict:
    resume_lines = _resume_fact_lines(data.get("resume_facts"))
    job_title = (data.get("job_title") or "").strip()
    job_description = (data.get("job_description") or "").strip()
    questions, skipped_opening = _select_main_questions(data.get("turns") or [])

    reviewed: list[dict] = []
    if heuristic:
        reviewer = "heuristic-keyword-overlap (DRY — NOT a gate decision)"
        for q in questions:
            v = _heuristic_review(q.text, resume_lines, job_title, job_description)
            reviewed.append(_review_row(q, v))
    else:
        import boto3

        from src.config import Config

        cfg = Config.load()
        client = boto3.client("bedrock-runtime", region_name=cfg.aws_region)
        reviewer = f"bedrock:{cfg.bedrock_model_id}"
        loop = asyncio.get_running_loop()
        for q in questions:
            v = await loop.run_in_executor(
                None,
                _bedrock_review,
                client,
                cfg.bedrock_model_id,
                q.text,
                resume_lines,
                job_title,
                job_description,
            )
            reviewed.append(_review_row(q, v))

    evaluated = len(reviewed)
    grounded = sum(1 for r in reviewed if r["grounded"])
    rate = (grounded / evaluated) if evaluated else 0.0
    # No evaluable questions cannot demonstrate grounding -> the gate cannot PASS on no evidence.
    verdict = "PASS" if (evaluated > 0 and rate >= PASS_THRESHOLD) else "FAIL"

    return {
        "mode": "grounding",
        "session_id": data.get("session_id"),
        "reviewer": reviewer,
        "evaluated": evaluated,
        "grounded": grounded,
        "grounding_rate": round(rate, 4),
        "pass_threshold": PASS_THRESHOLD,
        "verdict": verdict,
        "skipped_opening": skipped_opening,
        "resume_facts_available": len(resume_lines),
        "questions": reviewed,
    }


# --- Containment run (SC-005, T029) --------------------------------------------------------------


def _coach_turns(turns: list[dict]) -> list[dict]:
    """Coach turns in turn order, regardless of source shape.

    RDS rows carry a `speaker` field (coach|student) — keep only coach turns. The personalized
    harness (run_session.py) records coach turns only and omits `speaker`, so treat a missing
    speaker as a coach turn. Sorted by turn_index so the main->follow-up adjacency is honest."""
    out = [t for t in turns if (t.get("speaker") or "coach") == "coach"]
    return sorted(out, key=lambda t: t.get("turn_index", 0))


async def _run_containment(data: dict) -> dict:
    """SC-005: every follow-up turn shares its originating main question's archetype_id.

    Deterministic structural invariant (no LLM, off the gap clock). Walks the coach turns in order;
    the "current" archetype is set by each main (non-follow-up) turn, and every follow-up MUST match
    it — a probe that carries a different archetype_id (or none, when a main is active) has drifted
    into another competency, which breaks cross-session comparability (Principle V). A follow-up that
    appears before any main question is also a violation (nothing to contain it). ANY violation FAILS:
    this is a hard invariant, not a rate.
    """
    coach = _coach_turns(data.get("turns") or [])

    current_main: str | None = None
    current_main_turn: int | None = None
    followups = 0
    violations: list[dict] = []
    checked: list[dict] = []

    for t in coach:
        aid = t.get("archetype_id")
        aid = str(aid) if aid is not None else None
        is_fu = bool(t.get("is_followup"))
        ti = int(t.get("turn_index", -1))
        if not is_fu:
            # A main question (re)sets the competency a subsequent probe must stay within.
            current_main = aid
            current_main_turn = ti
            continue

        followups += 1
        ok = current_main is not None and aid is not None and aid == current_main
        row = {
            "turn_index": ti,
            "archetype_id": aid,
            "expected_archetype_id": current_main,
            "origin_main_turn": current_main_turn,
            "contained": ok,
        }
        checked.append(row)
        if not ok:
            if current_main is None:
                row["reason"] = "follow-up before any main question — nothing to contain it"
            elif aid is None:
                row["reason"] = "follow-up carries no archetype_id"
            else:
                row["reason"] = (
                    f"follow-up archetype {aid} != originating main archetype {current_main} "
                    "(drifted to another competency)"
                )
            violations.append(row)

    verdict = "PASS" if not violations else "FAIL"
    return {
        "mode": "containment",
        "session_id": data.get("session_id"),
        "coach_turns": len(coach),
        "followups_checked": followups,
        "violations": len(violations),
        "verdict": verdict,
        "violation_detail": violations,
        "followup_detail": checked,
    }


# --- Difficulty distinctness run (SC-004, T033) --------------------------------------------------


def _coach_question_texts(turns: list[dict]) -> list[str]:
    """Coach question text in turn order, from either source shape.

    RDS rows carry full `transcript`; the personalized harness records `reply_preview`. Either is a
    fair signal for the blind reviewer. The fixed scripted opener carries no tier signal, so it is
    dropped (it is identical across tiers and would only dilute the contrast)."""
    from src.reply.interface import OPENING_QUESTION

    opener = OPENING_QUESTION.strip()
    out: list[str] = []
    for t in _coach_turns(turns):
        text = (t.get("transcript") or t.get("reply_preview") or "").strip()
        if text and text != opener:
            out.append(text)
    return out


# Tier-profile signal phrases (heuristic only — DRY). A Difficult tier drills harder, injects
# curveballs, withholds hints, and is cooler in tone; an Easy tier is the opposite (see
# bank/seed/difficulty_profiles.sql). These let the pipeline be exercised without an LLM reviewer.
_PROBE_MARKERS = ("specifically", "exactly", "walk me through", "what was your role", "concrete",
                  "in detail", "precisely", "drill", "why did you", "how exactly")
_CURVEBALL_MARKERS = ("what if", "imagine", "under pressure", "suppose", "push back", "challenge",
                      "stress", "worst", "fails", "goes wrong")
_HINT_MARKERS = ("for example", "such as", "you might", "you could", "for instance", "perhaps",
                 "it's okay", "take your time", "no worries")
_WARMTH_MARKERS = ("great", "thanks", "well done", "wonderful", "excellent", "happy to", "relax",
                   "that's fine", "good job")


def _difficulty_signal(turns: list[dict]) -> dict:
    """A deterministic difficulty score for one transcript (higher == more Difficult-like).

    Primary signal is STRUCTURAL and tier-defining: how hard the funnel drilled — follow-ups per
    distinct archetype (probing_intensity is the tier's follow-up budget, so Difficult produces more
    follow-ups per competency than Easy). Falls back to text markers when structural facts are absent
    (e.g. an RDS transcript exported without is_followup). NOT a gate decision (DRY)."""
    coach = _coach_turns(turns)
    followups = sum(1 for t in coach if t.get("is_followup"))
    distinct_arch = len({t.get("archetype_id") for t in coach if t.get("archetype_id") is not None})
    followup_density = (followups / distinct_arch) if distinct_arch else 0.0

    blob = " ".join(_coach_question_texts(turns)).lower()
    probe = sum(blob.count(m) for m in _PROBE_MARKERS)
    curveball = sum(blob.count(m) for m in _CURVEBALL_MARKERS)
    hint = sum(blob.count(m) for m in _HINT_MARKERS)
    warmth = sum(blob.count(m) for m in _WARMTH_MARKERS)
    text_score = probe + curveball - hint - warmth

    # Structural density dominates when present (it is the tier's defining lever); text breaks ties.
    score = followup_density * 10.0 + text_score
    return {
        "score": round(score, 3),
        "followups": followups,
        "distinct_archetypes": distinct_arch,
        "followup_density": round(followup_density, 3),
        "probe": probe,
        "curveball": curveball,
        "hint": hint,
        "warmth": warmth,
    }


_DIFFICULTY_REVIEWER_SYSTEM = (
    "You are a blind reviewer of practice-interview transcripts. You are shown TWO transcripts, "
    "Transcript A and Transcript B, from the SAME candidate and the SAME target role, but each ran "
    "at a different difficulty tier: one EASY, one DIFFICULT. An Easy tier probes gently with brief "
    "follow-ups, offers hints/scaffolding, is warm and encouraging, avoids curveballs, and keeps "
    "questions general. A Difficult tier drills persistently for concrete specifics across several "
    "follow-ups, injects curveballs/stress angles, offers no hints, is neutral/businesslike, and goes "
    "deep into role-specific detail. Decide which transcript is the DIFFICULT one.\n"
    "Respond with ONLY a JSON object, no prose:\n"
    '{"difficult": "A"|"B", "rationale": <short string citing the tier signals you used>}'
)


def _build_difficulty_user_msg(text_a: str, text_b: str) -> str:
    return (
        f"TRANSCRIPT A (coach questions, in order):\n{text_a or '(empty)'}\n\n"
        f"TRANSCRIPT B (coach questions, in order):\n{text_b or '(empty)'}\n\n"
        "Which transcript ran at the DIFFICULT tier? Reply with the JSON object only."
    )


def _parse_difficulty_json(text: str) -> tuple[str | None, str]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None, "reviewer returned no parseable JSON"
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None, "reviewer JSON did not parse"
    pick = str(obj.get("difficult") or "").strip().upper()
    return (pick if pick in ("A", "B") else None), str(obj.get("rationale") or "")


def _bedrock_difficulty(client, model_id: str, text_a: str, text_b: str) -> tuple[str | None, str]:
    resp = client.converse(
        modelId=model_id,
        system=[{"text": _DIFFICULTY_REVIEWER_SYSTEM}],
        messages=[{"role": "user", "content": [{"text": _build_difficulty_user_msg(text_a, text_b)}]}],
        inferenceConfig={"maxTokens": 400, "temperature": 0},
    )
    return _parse_difficulty_json(resp["output"]["message"]["content"][0]["text"])


async def _run_difficulty(pairs: list[tuple[dict, dict]], heuristic: bool) -> dict:
    """SC-004: a blind reviewer identifies Easy vs Difficult across paired trials (>= 90%).

    Each pair is (easy_data, difficult_data) — the GROUND TRUTH. For each pair the two transcripts are
    presented as A/B with the presentation order ALTERNATED by pair index (no RNG — keeps the run
    reproducible while removing positional bias), the reviewer picks which is Difficult, and we score
    the identification against the ground truth. PASS iff the correct-identification rate >= 90% AND
    there is at least one pair (no evidence cannot pass).
    """
    client = model_id = None
    if heuristic:
        reviewer = "heuristic-tier-signal (DRY — NOT a gate decision)"
    else:
        import boto3

        from src.config import Config

        cfg = Config.load()
        client = boto3.client("bedrock-runtime", region_name=cfg.aws_region)
        model_id = cfg.bedrock_model_id
        reviewer = f"bedrock:{model_id}"
        loop = asyncio.get_running_loop()

    trials: list[dict] = []
    correct = 0
    for i, (easy_data, hard_data) in enumerate(pairs):
        easy_turns = easy_data.get("turns") or []
        hard_turns = hard_data.get("turns") or []
        # Alternate which tier is shown as "A" so the reviewer cannot exploit a fixed position.
        easy_is_a = (i % 2 == 0)
        a_turns, b_turns = (easy_turns, hard_turns) if easy_is_a else (hard_turns, easy_turns)
        actual_difficult = "B" if easy_is_a else "A"

        if heuristic:
            sig_a = _difficulty_signal(a_turns)
            sig_b = _difficulty_signal(b_turns)
            # Higher difficulty score == predicted Difficult; ties are an undecidable (wrong) trial.
            predicted = "A" if sig_a["score"] > sig_b["score"] else ("B" if sig_b["score"] > sig_a["score"] else None)
            rationale = f"A.score={sig_a['score']} vs B.score={sig_b['score']}"
            signals = {"A": sig_a, "B": sig_b}
        else:
            text_a = "\n".join(f"- {q}" for q in _coach_question_texts(a_turns))
            text_b = "\n".join(f"- {q}" for q in _coach_question_texts(b_turns))
            predicted, rationale = await loop.run_in_executor(
                None, _bedrock_difficulty, client, model_id, text_a, text_b
            )
            signals = None

        is_correct = predicted == actual_difficult
        correct += int(is_correct)
        trials.append({
            "pair_index": i,
            "easy_source": easy_data.get("session_id") or easy_data.get("_source"),
            "difficult_source": hard_data.get("session_id") or hard_data.get("_source"),
            "shown_easy_as": "A" if easy_is_a else "B",
            "predicted_difficult": predicted,
            "actual_difficult": actual_difficult,
            "correct": is_correct,
            "rationale": rationale,
            "signals": signals,
        })

    n = len(pairs)
    rate = (correct / n) if n else 0.0
    verdict = "PASS" if (n > 0 and rate >= PASS_THRESHOLD) else "FAIL"
    return {
        "mode": "difficulty",
        "reviewer": reviewer,
        "pairs": n,
        "correct": correct,
        "identification_rate": round(rate, 4),
        "pass_threshold": PASS_THRESHOLD,
        "verdict": verdict,
        "trials": trials,
    }


def _review_row(q: Question, v: GroundingVerdict) -> dict:
    return {
        "turn_index": q.turn_index,
        "question": q.text[:200],
        "grounded": v.grounded,
        "resume_fact": v.resume_fact,
        "job_requirement": v.job_requirement,
        "rationale": v.rationale,
    }


async def _load_data(args: argparse.Namespace) -> dict:
    """Resolve the session data from --transcript (offline) or --session (RDS)."""
    if args.transcript:
        return _load_transcript_file(args.transcript)
    if args.session:
        from src.config import Config

        database_url = args.database_url or Config.load().database_url
        if not database_url:
            raise SystemExit("DATABASE_URL is not set; pass --database-url or use --transcript.")
        return await _load_session_data(database_url, args.session)
    raise SystemExit("provide --session <id> (reads RDS) or --transcript <path> (offline).")


def _resolve_pair_ref(ref: str) -> dict:
    """Resolve one --pair entry (a run/transcript JSON) to its loaded data.

    An entry may be a literal path (ends in .json or contains a path separator) or a bare run tag,
    in which case it resolves to `runs/<tag>.json` — the file `run_session.py --tag <tag>` writes.
    The resolved source label is stamped on the data so the trial output cites where it came from.
    """
    candidates = [ref] if (ref.endswith(".json") or os.sep in ref) else [
        os.path.join("runs", f"{ref}.json"),
        f"{ref}.json",
        ref,
    ]
    for path in candidates:
        if os.path.exists(path):
            data = _load_transcript_file(path)
            data.setdefault("_source", data.get("tag") or os.path.basename(path))
            return data
    raise SystemExit(
        f"--pair entry {ref!r} not found (looked for {', '.join(candidates)}); run "
        f"`run_session.py --personalized --tag {ref}` first, or pass an explicit JSON path."
    )


async def _load_pairs(args: argparse.Namespace) -> list[tuple[dict, dict]]:
    """Build the (easy_data, difficult_data) pairs for SC-004 from --pair specs.

    Each --pair is `easy,difficult` (a run tag or JSON path each). May be repeated for multiple
    paired trials. Easy is always FIRST in the comma pair — that is the ground truth the reviewer is
    scored against (the reviewer never sees this ordering; presentation is alternated in _run_difficulty).
    """
    pairs: list[tuple[dict, dict]] = []
    for spec in args.pair:
        parts = [p.strip() for p in spec.split(",") if p.strip()]
        if len(parts) != 2:
            raise SystemExit(
                f"--pair must be 'easy,difficult' (two refs); got {spec!r} with {len(parts)} part(s)."
            )
        easy_ref, hard_ref = parts
        pairs.append((_resolve_pair_ref(easy_ref), _resolve_pair_ref(hard_ref)))
    return pairs


async def _amain(args: argparse.Namespace) -> int:
    if args.mode == "difficulty":
        if not args.pair:
            raise SystemExit(
                "--mode difficulty requires at least one --pair easy,difficult (a run tag or JSON "
                "path each); see quickstart Check D."
            )
        pairs = await _load_pairs(args)
        result = await _run_difficulty(pairs, heuristic=args.heuristic)
    elif args.mode == "containment":
        data = await _load_data(args)
        result = await _run_containment(data)
    else:
        data = await _load_data(args)
        result = await _run_grounding(data, heuristic=args.heuristic)

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))
    return 0 if result["verdict"] == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Blind-review grounding eval (SC-002) for a personalized session"
    )
    parser.add_argument("--mode", default="grounding", choices=["grounding", "difficulty", "containment"])
    parser.add_argument("--session", help="voice_session id to read transcript + grounding context from RDS")
    parser.add_argument("--transcript", help="offline path: a pre-exported transcript JSON (no RDS)")
    parser.add_argument(
        "--pair",
        action="append",
        default=[],
        metavar="EASY,DIFFICULT",
        help="--mode difficulty: a paired-trial spec 'easy,difficult' (each a run tag resolved as "
        "runs/<tag>.json, or an explicit JSON path). Easy is FIRST (the ground truth). Repeatable "
        "for multiple paired trials (SC-004 identification rate is computed across all pairs).",
    )
    parser.add_argument("--database-url", help="override DATABASE_URL for the RDS read")
    parser.add_argument(
        "--heuristic",
        action="store_true",
        help="use the deterministic keyword-overlap reviewer instead of the Bedrock LLM "
        "(DRY — exercises the pipeline without credentials; NOT a gate decision)",
    )
    parser.add_argument("--out", help="also write the verdict JSON to this path")
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
