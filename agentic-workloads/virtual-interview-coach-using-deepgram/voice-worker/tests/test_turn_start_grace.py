"""Unit tests for the barge-in grace window (false-barge-in fix).

Asserts that a transcript arriving WHILE the coach is speaking and WITHIN the grace window does NOT
start a (barge-in) user turn — the bug where a stale/late transcript of the student's own finished
turn cancelled the coach reply — while a transcript after the window (or while the bot is silent)
behaves like the base MinWords strategy.
"""

from __future__ import annotations

import time

import pytest

# Pipecat is only present in .venv-pipecat; skip cleanly in the rollback venv (conftest is lazy-safe).
pytest.importorskip("pipecat")

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    TranscriptionFrame,
)
from pipecat.turns.types import ProcessFrameResult

from src.turn_start_grace import GraceWindowMinWordsUserTurnStartStrategy

pytestmark = pytest.mark.asyncio


def _strategy(grace_secs=1.5, min_words=3):
    s = GraceWindowMinWordsUserTurnStartStrategy(min_words=min_words, grace_secs=grace_secs)
    started = {"n": 0}

    # on_user_turn_started is registered sync=True, so the handler must be a plain callable.
    def on_started(_s, _params):
        started["n"] += 1

    s.add_event_handler("on_user_turn_started", on_started)
    return s, started


def _txt(words: int) -> TranscriptionFrame:
    return TranscriptionFrame(text=" ".join(["word"] * words), user_id="u", timestamp="t")


async def test_suppresses_late_transcript_inside_grace_window():
    """Bot speaking + within grace window: a >=min_words transcript must NOT start a turn."""
    s, started = _strategy(grace_secs=1.5, min_words=3)
    await s.process_frame(BotStartedSpeakingFrame())
    res = await s.process_frame(_txt(7))  # 7 words, but stale/late inside the window
    assert started["n"] == 0, "late transcript inside grace window must not trigger a barge-in"
    assert res == ProcessFrameResult.CONTINUE


async def test_allows_barge_in_after_grace_window():
    """Bot speaking + past the grace window: a sustained >=min_words transcript DOES interrupt."""
    s, started = _strategy(grace_secs=0.05, min_words=3)
    await s.process_frame(BotStartedSpeakingFrame())
    time.sleep(0.06)  # let the (tiny) grace window elapse
    await s.process_frame(_txt(5))
    assert started["n"] == 1, "a genuine barge-in past the grace window must interrupt"


async def test_bot_silent_single_word_starts_turn_normally():
    """Bot NOT speaking: base behavior — even one word starts the (normal) user turn."""
    s, started = _strategy(grace_secs=1.5, min_words=3)
    await s.process_frame(BotStartedSpeakingFrame())
    await s.process_frame(BotStoppedSpeakingFrame())
    await s.process_frame(_txt(1))
    assert started["n"] == 1, "with the bot silent, normal turn-taking is unaffected"
