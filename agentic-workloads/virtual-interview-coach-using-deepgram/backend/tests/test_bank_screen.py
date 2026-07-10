"""Tests for the automated bank screen (bank/screen.py) — pure screen_drafts logic, no DB/Bedrock.

Proves the deterministic gate: format/STAR rejects, near-duplicate collapse within a slot (first-seen
in id order wins), and that distinct questions in the same slot are both kept (depth survives)."""

from __future__ import annotations

import uuid

from bank.screen import screen_drafts, _normalize


def _row(rid, prompt, probes, competency="role_specific", difficulty="moderate", role_family="software_engineering"):
    return {
        "id": uuid.UUID(rid) if isinstance(rid, str) and "-" in rid else rid,
        "category": "domain",
        "competency": competency,
        "difficulty": difficulty,
        "role_family": role_family,
        "prompt_template": prompt,
        "follow_up_prompts": probes,
    }


def test_passes_well_formed_draft():
    rows = [_row(1, "Tell me about a time you debugged a hard production bug. What happened?",
                 ["What was the root cause?", "How did you verify the fix?"])]
    plan = screen_drafts(rows)
    assert plan["approve"] == ["1"]
    assert plan["retire"] == []


def test_rejects_too_short_and_too_few_probes():
    rows = [
        _row(1, "Too short.", ["a", "b"]),                       # length fail
        _row(2, "Tell me about a meaningful design trade-off you made and why it mattered.", ["only one"]),  # probes fail
    ]
    plan = screen_drafts(rows)
    assert plan["approve"] == []
    reasons = dict(plan["retire"])
    assert "length" in reasons["1"]
    assert "follow-up probe" in reasons["2"]


def test_near_duplicate_within_slot_is_retired_first_seen_wins():
    rows = [
        _row(1, "Walk me through a time you had to redesign an API that multiple teams depended on.",
             ["What constraints?", "How did you migrate consumers?"]),
        _row(2, "Walk me through a time you had to redesign an API that several teams depended upon.",
             ["What were the constraints?", "How did you migrate the consumers?"]),
    ]
    plan = screen_drafts(rows)
    assert plan["approve"] == ["1"]            # first in id order kept
    assert plan["retire"] == [("2", "near-duplicate within slot")]


def test_distinct_questions_in_same_slot_both_kept():
    rows = [
        _row(1, "Tell me about a time you debugged a hard production incident under pressure.",
             ["Root cause?", "How verified?"]),
        _row(2, "Walk me through a significant architecture trade-off you had to make.",
             ["What were the options?", "What did you optimize for?"]),
    ]
    plan = screen_drafts(rows)
    assert set(plan["approve"]) == {"1", "2"}   # different topics -> depth survives
    assert plan["retire"] == []


def test_same_text_different_slot_is_not_a_duplicate():
    # Identical prompt but different difficulty -> different group -> both kept (slots are independent).
    p = "Tell me about a time you had to make a difficult prioritisation call under real constraints."
    probes = ["What did you cut?", "How did you communicate it?"]
    rows = [
        _row(1, p, probes, difficulty="moderate"),
        _row(2, p, probes, difficulty="difficult"),
    ]
    plan = screen_drafts(rows)
    assert set(plan["approve"]) == {"1", "2"}


def test_normalize_strips_punctuation_and_case():
    assert _normalize("Walk  me, THROUGH a time!") == "walk me through a time"
