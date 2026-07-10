"""Tests for BedrockDirectReplyGenerator._build_messages (Converse message validity).

Bedrock Converse rejects empty-text content blocks and a conversation that does not END on a `user`
turn. The end-of-interview DEBRIEF turn (F004) has student_text="" and a history ending on a coach turn
— previously that produced a trailing empty `user` block -> ValidationException (the spoken debrief
silently fell back to the fixed closing). These prove the request is always structurally valid.
"""

from __future__ import annotations

from src.config import Config
from src.reply.bedrock_direct import BedrockDirectReplyGenerator
from src.reply.interface import TurnContext, Utterance


def _gen() -> BedrockDirectReplyGenerator:
    return BedrockDirectReplyGenerator(Config.load())


def _assert_valid(messages: list[dict]) -> None:
    assert messages, "messages must not be empty"
    assert messages[-1]["role"] == "user", "Converse requires the conversation to end on a user turn"
    for m in messages:
        for block in m["content"]:
            assert block["text"].strip(), "no empty-text content blocks allowed (ValidationException)"


def test_debrief_turn_builds_valid_messages():
    # The exact failing shape: student_text="" and history ending on a COACH turn.
    ctx = TurnContext(
        session_id="s",
        student_text="",
        history=[
            Utterance(speaker="coach", text="Tell me about a hard problem you solved."),
            Utterance(speaker="student", text="I debugged a payments reconciliation pipeline."),
            Utterance(speaker="coach", text="That's everything I wanted to cover."),
        ],
        is_debrief=True,
    )
    msgs = _gen()._build_messages(ctx)
    _assert_valid(msgs)
    # the trailing user turn is the debrief nudge (the system prompt carries the real instruction)
    assert "closing thoughts" in msgs[-1]["content"][0]["text"].lower()


def test_normal_turn_appends_student_text():
    ctx = TurnContext(
        session_id="s",
        student_text="Here is my answer with enough words.",
        history=[Utterance(speaker="coach", text="First question?")],
    )
    msgs = _gen()._build_messages(ctx)
    _assert_valid(msgs)
    assert msgs[-1]["content"][0]["text"] == "Here is my answer with enough words."
    assert msgs[0] == {"role": "assistant", "content": [{"text": "First question?"}]}


def test_empty_history_and_empty_student_text_still_valid():
    ctx = TurnContext(session_id="s", student_text="", history=[], is_debrief=False)
    msgs = _gen()._build_messages(ctx)
    _assert_valid(msgs)  # falls back to a single benign user nudge


def test_blank_utterances_are_skipped():
    ctx = TurnContext(
        session_id="s",
        student_text="real answer",
        history=[
            Utterance(speaker="coach", text="  "),       # whitespace-only -> dropped
            Utterance(speaker="student", text=""),         # empty -> dropped
            Utterance(speaker="coach", text="A question?"),
        ],
    )
    msgs = _gen()._build_messages(ctx)
    _assert_valid(msgs)
    assert len(msgs) == 2  # the question + the student's real answer
