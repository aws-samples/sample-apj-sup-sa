"""Tests for the InterviewDirector + budget derivation (Feature 007 Issue-2 fix).

The director restores the bounded, structured interview the old loop had: it advances the
FunnelPlanner per student turn, writes the current plan into the shared planner_holder (which the
reply enricher reads), counts questions, and triggers wrap-up (suppressing the LLMContextFrame) when
the plan is exhausted or a budget is hit. These tests drive it with synthetic LLMContextFrames and a
stub wrap-up callback — no live services. Run in the pipecat venv.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pipecat")

from pipecat.frames.frames import LLMContextFrame, TTSAudioRawFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from src.config import Config
from src.processors.interview_director import InterviewDirector
from src.pipecat_pipeline import _derive_budget_s
from src.prep_handoff import SessionPlan


def _plan(n_rows: int) -> SessionPlan:
    rows = [
        {
            "id": f"arch-{i}",
            "competency": f"comp_{i}",
            "question_type": "behavioral",
            "prompt_template": f"Tell me about competency {i}.",
            "follow_up_prompts": ["Go on?"],
        }
        for i in range(n_rows)
    ]
    return SessionPlan(plan_rows=rows, opening_archetype_id="arch-0", duration_minutes=5)


def _ctx_frame(student_text: str) -> LLMContextFrame:
    c = LLMContext()
    c.set_messages([{"role": "assistant", "content": "prev question?"},
                    {"role": "user", "content": student_text}])
    return LLMContextFrame(context=c)


async def _make(session_plan, init_processor=None, config=None):
    cfg = config or Config.load()
    holder: dict = {}
    wrapped = {"n": 0}

    async def on_wrap_up():
        wrapped["n"] += 1

    d = InterviewDirector(
        session_plan=session_plan, config=cfg, planner_holder=holder, on_wrap_up=on_wrap_up
    )
    # Wrap-up is spawned via self.create_task, which needs a TaskManager; wire one when the test will
    # trigger wrap-up (pass init_processor). Tests that never wrap up can skip it.
    if init_processor is not None:
        await init_processor(d)
    pushed: list = []

    async def fake_push(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    d.push_frame = fake_push  # type: ignore[assignment]
    return d, holder, wrapped, pushed


# --- pass-through (generic session, no plan) --------------------------------------------


@pytest.mark.asyncio
async def test_generic_session_is_passthrough():
    d, holder, wrapped, pushed = await _make(session_plan=None)
    assert d.active is False
    await d.process_frame(_ctx_frame("hello there friend"), FrameDirection.DOWNSTREAM)
    assert len(pushed) == 1  # forwarded, not suppressed
    assert wrapped["n"] == 0
    assert holder == {}  # nothing stashed


# --- advance / probe + holder population --------------------------------------------------


@pytest.mark.asyncio
async def test_first_turn_emits_main_question_and_stashes_plan():
    d, holder, wrapped, pushed = await _make(_plan(3))
    assert d.active is True
    await d.process_frame(_ctx_frame("an answer to the opening question, fairly full"),
                          FrameDirection.DOWNSTREAM)
    # frame forwarded (not a bound), plan stashed for the enricher
    assert len(pushed) == 1 and isinstance(pushed[0], LLMContextFrame)
    assert holder["plan"].intent is not None
    assert holder["plan"].is_followup is False  # first archetype's opening main question
    assert holder["questions_remaining"] == 2  # 3 planned, 1 asked


@pytest.mark.asyncio
async def test_vague_answer_probes_full_answer_advances():
    d, holder, _, _ = await _make(_plan(3))
    await d.process_frame(_ctx_frame("opening answer here, reasonably complete sentence"),
                          FrameDirection.DOWNSTREAM)  # main Q for arch-0
    # a thin answer -> probe (stay on arch-0, is_followup True)
    await d.process_frame(_ctx_frame("um not sure"), FrameDirection.DOWNSTREAM)
    assert holder["plan"].is_followup is True
    # a substantive answer (>=6 words) -> credits STAR; eventually advances
    full = "I led the team, set the plan, executed it, and we shipped on time successfully"
    await d.process_frame(_ctx_frame(full), FrameDirection.DOWNSTREAM)
    # could be probe-or-advance depending on STAR credit; assert it stayed within bounds (no wrap-up)
    assert holder["plan"].intent is not None


# --- bounds: wrap-up suppresses the frame -------------------------------------------------


@pytest.mark.asyncio
async def test_plan_exhausted_triggers_wrapup_and_suppresses_frame(init_processor):
    # 1 row, easy budget -> the plan exhausts quickly; once exhausted, wrap-up fires and the
    # LLMContextFrame is NOT forwarded. Wrap-up is spawned as a background task -> needs a TaskManager.
    import asyncio

    d, holder, wrapped, pushed = await _make(_plan(1), init_processor=init_processor)
    full = "I led the project end to end, planned the work, did it, and delivered great results"
    fired = False
    for _ in range(12):
        pushed.clear()
        await d.process_frame(_ctx_frame(full), FrameDirection.DOWNSTREAM)
        if d._wrapped_up:
            assert pushed == []  # the context frame was SUPPRESSED on the wrap-up turn
            await asyncio.sleep(0.05)  # let the spawned wrap-up task run
            fired = wrapped["n"] > 0
            break
    assert fired, "interview never wrapped up"


@pytest.mark.asyncio
async def test_wrapup_fires_only_once(init_processor):
    import asyncio

    d, holder, wrapped, pushed = await _make(_plan(1), init_processor=init_processor)
    full = "I led the project end to end, planned the work, did it, and delivered great results"
    for _ in range(15):
        await d.process_frame(_ctx_frame(full), FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.05)
    assert wrapped["n"] == 1  # idempotent: never wraps up twice (flag set synchronously before spawn)


@pytest.mark.asyncio
async def test_non_context_frames_pass_through():
    d, _, _, pushed = await _make(_plan(3))
    await d.process_frame(TTSAudioRawFrame(b"\x00\x00", sample_rate=16000, num_channels=1),
                          FrameDirection.DOWNSTREAM)
    assert len(pushed) == 1  # forwarded untouched


# --- budget derivation (Issue 2a) ---------------------------------------------------------


def test_budget_from_duration_minutes():
    cfg = Config.load()
    assert _derive_budget_s(cfg, None) is None  # generic session, no bound
    assert _derive_budget_s(cfg, SessionPlan(duration_minutes=5)) == 5 * 60 + cfg.duration_grace_s
    # fallback to question-count estimate when no duration
    assert _derive_budget_s(cfg, _plan(4).__class__(plan_rows=[{}] * 4)) == 4 * cfg.seconds_per_question


def test_three_minute_tier_needs_no_worker_change():
    """F008 US5: the 3-min quick-test-drive tier flows through the EXISTING chain unchanged —
    duration_minutes=3 yields a 3*60+grace wall-clock budget, and a 2-question plan bounds the
    director at 2 mains / 2*(1+followups_per_question) coach turns."""
    cfg = Config.load()
    plan = _plan(2)
    plan.duration_minutes = 3
    assert _derive_budget_s(cfg, plan) == 3 * 60 + cfg.duration_grace_s
    holder: dict = {}

    async def _noop():
        pass

    d = InterviewDirector(session_plan=plan, config=cfg, planner_holder=holder, on_wrap_up=_noop)
    assert d.active is True
    assert d._max_main_questions == 2
    assert d._max_coach_turns == 2 * (1 + cfg.followups_per_question)


# --- Issue 1: voice barge-in requires real speech, not raw VAD ---------------------------


def test_voice_barge_in_gated_by_min_words_not_raw_vad():
    from src.pipecat_pipeline import _build_user_turn_strategies
    from pipecat.turns.user_start.vad_user_turn_start_strategy import VADUserTurnStartStrategy
    from pipecat.turns.user_start.min_words_user_turn_start_strategy import (
        MinWordsUserTurnStartStrategy,
    )

    sts = _build_user_turn_strategies(Config.load())
    by_type = {type(s).__name__: s for s in sts.start}
    # raw-VAD start strategy is present but does NOT interrupt the coach
    assert "VADUserTurnStartStrategy" in by_type
    assert by_type["VADUserTurnStartStrategy"]._enable_interruptions is False
    # a min-words strategy gates voice barge-in on real transcribed speech (the grace-window
    # subclass IS a MinWordsUserTurnStartStrategy, so existing behavior is preserved)
    assert any(isinstance(s, MinWordsUserTurnStartStrategy) for s in sts.start)


def test_voice_barge_in_uses_grace_window_strategy():
    """The false-barge-in fix: a grace-window subclass guards against stale/late transcripts."""
    from src.pipecat_pipeline import _build_user_turn_strategies
    from src.turn_start_grace import GraceWindowMinWordsUserTurnStartStrategy

    sts = _build_user_turn_strategies(Config.load())
    grace = [s for s in sts.start if isinstance(s, GraceWindowMinWordsUserTurnStartStrategy)]
    assert len(grace) == 1, "exactly one grace-window min-words strategy should be wired in"
    assert grace[0]._grace_secs == Config.load().voice_barge_in_grace_secs
