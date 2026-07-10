"""Voice / communication metrics (FR-307 / R4).

Computed from the data the interview already persists — transcript text + per-turn timing
(started_at/ended_at). NO audio is needed (audio recording/playback is F006). When timing is missing or
zero, pace/pause metrics degrade to None rather than blocking the report (spec Assumption). These
metrics are descriptive (they inform the Communication/Confidence narrative) — they are NOT blended into
the headline rubric score.

No raw transcript text is logged (Principle III).
"""

from __future__ import annotations

import re

# Common spoken fillers (whole-word match). Lowercased.
_FILLERS = ("um", "uh", "er", "ah", "like", "you know", "i mean", "sort of", "kind of", "basically",
            "literally", "actually", "so yeah", "right")
# Hedging / low-confidence phrases.
_HEDGES = ("i think", "i guess", "maybe", "probably", "perhaps", "sort of", "kind of", "i'm not sure",
           "i suppose", "possibly", "might be", "i feel like")


def _student_turns(turns: list[dict]) -> list[dict]:
    return [t for t in turns if t.get("speaker") == "student"]


def _word_count(text: str) -> int:
    return len([w for w in re.findall(r"[a-zA-Z']+", text or "")])


def _count_phrases(text: str, phrases) -> int:
    low = f" {text.lower()} "
    n = 0
    for p in phrases:
        # whole-word/phrase boundaries so "like" doesn't match "likely"
        n += len(re.findall(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", low))
    return n


def _turn_seconds(turn: dict) -> float | None:
    s, e = turn.get("started_at"), turn.get("ended_at")
    if s is None or e is None:
        return None
    try:
        return max(0.0, (e - s).total_seconds())
    except Exception:  # noqa: BLE001 - non-datetime values -> no timing
        return None


def compute_metrics(turns: list[dict], long_pause_ms: int = 3000) -> dict:
    """Return the voice/communication metrics dict for report.metrics.

    Keys: filler_count, wpm, long_pauses, conciseness, hedging_rate, responsiveness. wpm/long_pauses are
    None when per-turn timing is unavailable (graceful degrade)."""
    students = _student_turns(turns)
    total_words = sum(_word_count(t.get("transcript", "")) for t in students)
    filler_count = sum(_count_phrases(t.get("transcript", ""), _FILLERS) for t in students)
    hedge_count = sum(_count_phrases(t.get("transcript", ""), _HEDGES) for t in students)

    # Speaking pace: total student words / total student speaking seconds (when timing present).
    speaking_secs = 0.0
    have_timing = False
    for t in students:
        sec = _turn_seconds(t)
        if sec is not None:
            speaking_secs += sec
            have_timing = True
    # Guard against degenerate timing (e.g. started_at == ended_at, which happens when the writer
    # stamps both at persist time): a near-zero denominator yields an absurd wpm. Require a sane
    # minimum total speaking time AND a plausible result, else report null (graceful degrade — we
    # simply don't have reliable timing for this session). FR-307 / R4.
    wpm = None
    if have_timing and speaking_secs >= 1.0 and total_words > 0:
        candidate = round(total_words / (speaking_secs / 60.0), 1)
        if 20 <= candidate <= 400:   # plausible human speaking-pace band
            wpm = candidate

    # Long pauses only when timing is real (same degenerate-timing guard).
    long_pauses = _long_pauses(turns, long_pause_ms) if (have_timing and speaking_secs >= 1.0) else None

    # Conciseness: median words per student answer (descriptive — a band, not a grade).
    answer_lengths = [_word_count(t.get("transcript", "")) for t in students if t.get("transcript")]
    conciseness = round(sum(answer_lengths) / len(answer_lengths), 1) if answer_lengths else 0.0

    hedging_rate = round((hedge_count / total_words) * 100, 2) if total_words else 0.0

    return {
        "filler_count": filler_count,
        "wpm": wpm,
        "long_pauses": long_pauses,
        "conciseness": conciseness,        # avg words per answer
        "hedging_rate": hedging_rate,      # hedges per 100 words
        "responsiveness": _responsiveness(turns),
    }


def _long_pauses(turns: list[dict], long_pause_ms: int) -> int:
    """Count gaps between the end of one turn and the start of the next that exceed the threshold."""
    threshold = long_pause_ms / 1000.0
    count = 0
    ordered = sorted(turns, key=lambda t: t.get("turn_index", 0))
    for prev, cur in zip(ordered, ordered[1:]):
        pe, cs = prev.get("ended_at"), cur.get("started_at")
        if pe is None or cs is None:
            continue
        try:
            gap = (cs - pe).total_seconds()
        except Exception:  # noqa: BLE001
            continue
        if gap >= threshold:
            count += 1
    return count


def _responsiveness(turns: list[dict]) -> str:
    """Coarse read of whether follow-up answers added new specifics. Structural, from is_followup +
    answer length — not a score. Returns a short descriptor."""
    followup_answers = []
    ordered = sorted(turns, key=lambda t: t.get("turn_index", 0))
    for prev, cur in zip(ordered, ordered[1:]):
        if prev.get("speaker") == "coach" and prev.get("is_followup") and cur.get("speaker") == "student":
            followup_answers.append(_word_count(cur.get("transcript", "")))
    if not followup_answers:
        return "no_followups"
    avg = sum(followup_answers) / len(followup_answers)
    if avg >= 30:
        return "elaborated"          # follow-ups drew out substantial new detail
    if avg >= 12:
        return "adequate"
    return "thin"                    # follow-ups got short, low-detail answers
