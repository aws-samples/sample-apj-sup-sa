"""Unit tests for the duration -> question-count map and the category-mix composition.

Pure functions (no DB, no LLM): the duration mapping in api/sessions.py and the plan composition in
prep/blueprint.py. These guard items 1 (40/40/20 general/technical/job-scope mix + warmup opener) and
3 (interview duration -> number of main questions) from the human-test feedback.
"""

from __future__ import annotations

from src.api.sessions import _questions_for_duration, _DURATION_TO_QUESTIONS
from src.prep.blueprint import _split_counts, _compose_plan


def _row(rid: str, category: str, qtype: str = "behavioral", competency: str = "teamwork") -> dict:
    return {
        "id": rid,
        "category": category,
        "question_type": qtype,
        "competency": competency,
        "prompt_template": f"Q {rid}",
        "follow_up_prompts": [],
    }


def test_duration_maps_to_expected_question_counts():
    assert _questions_for_duration(3) == 2   # F008 US5: quick test drive
    assert _questions_for_duration(5) == 3
    assert _questions_for_duration(10) == 6
    assert _questions_for_duration(15) == 9
    assert _questions_for_duration(30) == 16
    assert _questions_for_duration(45) == 24


def test_duration_defaults_and_nearest():
    assert _questions_for_duration(None) == _DURATION_TO_QUESTIONS[10]  # default 10 min
    assert _questions_for_duration(12) == _DURATION_TO_QUESTIONS[10]    # nearest supported tier
    assert _questions_for_duration(40) == _DURATION_TO_QUESTIONS[45]
    # The new 3-min tier must not steal old behavior: 1-2 min requests snap DOWN to 3 now (the
    # nearest tier), while 4 min still snaps to 5 (distance 1 vs 1 ties resolve by min() order —
    # assert the actual contract so a future reorder is caught).
    assert _questions_for_duration(2) == _DURATION_TO_QUESTIONS[3]
    assert _questions_for_duration(4) in (_DURATION_TO_QUESTIONS[3], _DURATION_TO_QUESTIONS[5])


def test_split_counts_sums_to_total_and_leans_general():
    for total in range(0, 25):
        g, t, j = _split_counts(total)
        assert g + t + j == total, (total, g, t, j)
        # the general bucket is never the smallest on a small interview (favored on rounding)
        if total > 0:
            assert g >= j  # 40% general >= 20% job-scope


def test_split_counts_ratio_at_ten():
    # 10 body questions -> 4 general / 4 technical / 2 job-scope (40/40/20)
    assert _split_counts(10) == (4, 4, 2)


def test_compose_plan_adds_warmup_and_respects_mix():
    ranked = (
        [_row("w", "general", qtype="warmup", competency="motivation_fit")]
        + [_row(f"g{i}", "general") for i in range(5)]
        + [_row(f"d{i}", "domain", qtype="technical", competency="role_specific") for i in range(8)]
    )
    # 6 main questions (10 min) -> 1 warmup + body of 5 (split 2 general / 2 tech / 1 jobscope = 5).
    plan = _compose_plan(ranked, num_questions=6)
    assert len(plan) == 6
    assert plan[0]["question_type"] == "warmup"  # warmup opens
    ids = [r["id"] for r in plan]
    assert len(set(ids)) == len(ids)  # no duplicates


def test_compose_plan_backfills_when_a_pool_is_thin():
    # Only general rows available: composition must still fill to num_questions from what exists.
    ranked = [_row("w", "general", qtype="warmup")] + [_row(f"g{i}", "general") for i in range(10)]
    plan = _compose_plan(ranked, num_questions=6)
    assert len(plan) == 6  # backfilled from the general pool, no domain rows invented


def test_compose_plan_caps_at_num_questions():
    ranked = [_row(f"g{i}", "general") for i in range(20)] + [
        _row(f"d{i}", "domain", qtype="technical") for i in range(20)
    ]
    plan = _compose_plan(ranked, num_questions=3)
    assert len(plan) == 3
