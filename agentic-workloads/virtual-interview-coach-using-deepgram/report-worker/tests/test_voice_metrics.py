"""Voice-metrics unit tests (SC-005) — no model, no DB. Includes graceful degrade without timing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.voice_metrics import compute_metrics

_T0 = datetime(2026, 6, 6, 10, 0, 0, tzinfo=timezone.utc)


def _ts(offset_s: float) -> datetime:
    return _T0 + timedelta(seconds=offset_s)


def test_filler_and_hedging_counts():
    turns = [
        {"turn_index": 1, "speaker": "student",
         "transcript": "Um, I think I basically, you know, sort of handled it."},
    ]
    m = compute_metrics(turns)
    # um, basically, you know, sort of  -> 4 fillers; "i think", "sort of" -> hedges
    assert m["filler_count"] >= 4
    assert m["hedging_rate"] > 0


def test_wpm_and_pauses_with_timing():
    turns = [
        {"turn_index": 0, "speaker": "coach", "transcript": "Q1", "started_at": _ts(0), "ended_at": _ts(2)},
        # 10 words over 30s -> 20 wpm
        {"turn_index": 1, "speaker": "student",
         "transcript": "one two three four five six seven eight nine ten",
         "started_at": _ts(2), "ended_at": _ts(32)},
        # 5s gap (>3s threshold) before the next coach turn -> 1 long pause
        {"turn_index": 2, "speaker": "coach", "transcript": "Q2", "started_at": _ts(37), "ended_at": _ts(39)},
        {"turn_index": 3, "speaker": "student", "transcript": "short answer here now",
         "started_at": _ts(39), "ended_at": _ts(45)},
    ]
    m = compute_metrics(turns, long_pause_ms=3000)
    # student words = 10 + 4 = 14 over speaking secs = 30 + 6 = 36 -> 14/(36/60) = 23.3 wpm
    assert m["wpm"] == 23.3
    assert m["long_pauses"] == 1


def test_graceful_degrade_without_timing():
    turns = [
        {"turn_index": 1, "speaker": "student", "transcript": "no timestamps on this turn at all here"},
    ]
    m = compute_metrics(turns)
    assert m["wpm"] is None          # no timing -> null, not a crash
    assert m["long_pauses"] is None
    assert m["filler_count"] == 0    # text metrics still computed


def test_responsiveness_descriptor():
    turns = [
        {"turn_index": 0, "speaker": "coach", "transcript": "main q", "is_followup": False},
        {"turn_index": 1, "speaker": "student", "transcript": "ok"},
        {"turn_index": 2, "speaker": "coach", "transcript": "follow up probe", "is_followup": True},
        {"turn_index": 3, "speaker": "student",
         "transcript": "Here is a much longer follow up answer that adds many new concrete specific "
                       "details about exactly what I personally did and the measurable result it produced "
                       "for the business overall after we shipped it to production last quarter."},
    ]
    m = compute_metrics(turns)
    # >=30-word follow-up answer -> "elaborated"
    assert m["responsiveness"] == "elaborated"
