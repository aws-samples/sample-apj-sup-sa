"""Unit tests for the ReplyGenerator -> Pipecat LLMService adapter (Feature 007, T009).

The adapter must, offline (no live provider, no real pipeline):
  - reconstruct our TurnContext from Pipecat's OpenAI-shaped LLMContext (last user msg = the current
    student turn; earlier msgs = the running transcript; system msgs ignored),
  - push the exact Pipecat LLM frame sequence (start -> N text frames -> end),
  - apply the per-session personalization enricher to the reconstructed context,
  - propagate cancellation (barge-in) and close the provider generator.

Run in the isolated Pipecat venv: `source .venv-pipecat/bin/activate && pytest tests/test_pipecat_adapter.py`
"""

from __future__ import annotations

import asyncio

import pytest

# Skip the whole module in a venv without pipecat (the G1 rollback venv) rather than erroring.
pytest.importorskip("pipecat")

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext

from src.reply.interface import JobScope, TurnContext
from src.reply.pipecat_adapter import ReplyGeneratorLLMService


class _FakeReply:
    """Records the TurnContext it was handed and yields a fixed chunk stream."""

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.last_ctx: TurnContext | None = None
        self.closed = False

    async def stream(self, ctx: TurnContext):
        self.last_ctx = ctx
        try:
            for c in self.chunks:
                yield c
        finally:
            self.closed = True


def _make_service(reply, enrich=None) -> ReplyGeneratorLLMService:
    svc = ReplyGeneratorLLMService(reply, enrich_turn_context=enrich)
    pushed: list = []

    async def fake_push(frame, direction=None):
        pushed.append(frame)

    async def _noop(*a, **k):
        return None

    svc.push_frame = fake_push  # type: ignore[assignment]
    svc.start_processing_metrics = _noop  # type: ignore[assignment]
    svc.stop_processing_metrics = _noop  # type: ignore[assignment]
    svc.start_ttfb_metrics = _noop  # type: ignore[assignment]
    svc.stop_ttfb_metrics = _noop  # type: ignore[assignment]
    svc._pushed = pushed  # type: ignore[attr-defined]
    return svc


def _capture_via_make(svc) -> list:
    """Attach a push/metrics capture to an already-constructed service; return the pushed list."""
    pushed: list = []

    async def fake_push(frame, direction=None):
        pushed.append(frame)

    async def _noop(*a, **k):
        return None

    svc.push_frame = fake_push  # type: ignore[assignment]
    svc.start_processing_metrics = _noop  # type: ignore[assignment]
    svc.stop_processing_metrics = _noop  # type: ignore[assignment]
    svc.start_ttfb_metrics = _noop  # type: ignore[assignment]
    svc.stop_ttfb_metrics = _noop  # type: ignore[assignment]
    return pushed


def _ctx(messages: list[dict]) -> LLMContext:
    c = LLMContext()
    c.set_messages(messages)
    return c


@pytest.mark.asyncio
async def test_frame_sequence_and_context_reconstruction():
    reply = _FakeReply(["Right. ", "Tell me ", "about a challenge."])
    svc = _make_service(reply)
    await svc._process_context(
        _ctx(
            [
                {"role": "system", "content": "persona prompt"},
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Tell me about yourself."},
                {"role": "user", "content": "I am a final-year CS student."},
            ]
        )
    )
    pushed = svc._pushed  # type: ignore[attr-defined]
    assert isinstance(pushed[0], LLMFullResponseStartFrame)
    assert isinstance(pushed[-1], LLMFullResponseEndFrame)
    texts = [f.text for f in pushed if isinstance(f, LLMTextFrame)]
    assert texts == ["Right. ", "Tell me ", "about a challenge."]
    # last user message becomes the current student turn; earlier msgs are history; system ignored
    assert reply.last_ctx.student_text == "I am a final-year CS student."
    assert [(u.speaker, u.text) for u in reply.last_ctx.history] == [
        ("student", "Hi"),
        ("coach", "Tell me about yourself."),
    ]
    assert reply.closed is True


@pytest.mark.asyncio
async def test_empty_chunks_are_skipped():
    reply = _FakeReply(["", "Hello.", ""])
    svc = _make_service(reply)
    await svc._process_context(_ctx([{"role": "user", "content": "hi"}]))
    texts = [f.text for f in svc._pushed if isinstance(f, LLMTextFrame)]  # type: ignore[attr-defined]
    assert texts == ["Hello."]


@pytest.mark.asyncio
async def test_enricher_applied_to_turn_context():
    reply = _FakeReply(["ok"])

    def enrich(ctx: TurnContext) -> TurnContext:
        ctx.job_scope = JobScope(title="Backend Engineer", key_requirements=["Python"])
        ctx.resume_highlights = ["Built a microservice"]
        return ctx

    svc = _make_service(reply, enrich=enrich)
    await svc._process_context(_ctx([{"role": "user", "content": "hi"}]))
    assert reply.last_ctx.job_scope is not None
    assert reply.last_ctx.job_scope.title == "Backend Engineer"
    assert reply.last_ctx.resume_highlights == ["Built a microservice"]


@pytest.mark.asyncio
async def test_list_content_parts_are_flattened():
    reply = _FakeReply(["ok"])
    svc = _make_service(reply)
    await svc._process_context(
        _ctx([{"role": "user", "content": [{"type": "text", "text": "part one"}]}])
    )
    assert reply.last_ctx.student_text == "part one"


@pytest.mark.asyncio
async def test_error_speaks_contained_fallback():
    class _ErrReply:
        async def stream(self, ctx):
            raise RuntimeError("provider 5xx")
            yield  # pragma: no cover - makes this a generator

    svc = ReplyGeneratorLLMService(_ErrReply(), fallback_text=lambda: "Staying on teamwork — go on.")
    pushed = _capture_via_make(svc)
    await svc._process_context(_ctx([{"role": "user", "content": "hi"}]))
    texts = [f.text for f in pushed if isinstance(f, LLMTextFrame)]
    # the turn is NOT dropped: a contained fallback probe is spoken
    assert texts == ["Staying on teamwork — go on."]


@pytest.mark.asyncio
async def test_stall_past_budget_speaks_fallback():
    import asyncio

    class _StallReply:
        async def stream(self, ctx):
            await asyncio.sleep(5)
            yield "too late"  # pragma: no cover

    svc = ReplyGeneratorLLMService(
        _StallReply(), turn_budget_s=0.05, fallback_text=lambda: "Let's keep going — what happened next?"
    )
    pushed = _capture_via_make(svc)
    await svc._process_context(_ctx([{"role": "user", "content": "hi"}]))
    texts = [f.text for f in pushed if isinstance(f, LLMTextFrame)]
    assert texts == ["Let's keep going — what happened next?"]


@pytest.mark.asyncio
async def test_empty_output_speaks_fallback():
    class _EmptyReply:
        async def stream(self, ctx):
            if False:  # pragma: no cover
                yield ""
            return

    svc = ReplyGeneratorLLMService(_EmptyReply(), fallback_text=lambda: "Tell me more about that.")
    pushed = _capture_via_make(svc)
    await svc._process_context(_ctx([{"role": "user", "content": "hi"}]))
    texts = [f.text for f in pushed if isinstance(f, LLMTextFrame)]
    assert texts == ["Tell me more about that."]


@pytest.mark.asyncio
async def test_healthy_reply_no_fallback():
    svc = ReplyGeneratorLLMService(
        _FakeReply(["All", " good", "."]), fallback_text=lambda: "FALLBACK"
    )
    pushed = _capture_via_make(svc)
    await svc._process_context(_ctx([{"role": "user", "content": "hi"}]))
    texts = [f.text for f in pushed if isinstance(f, LLMTextFrame)]
    assert texts == ["All", " good", "."]
    assert "FALLBACK" not in texts


@pytest.mark.asyncio
async def test_cancellation_propagates_and_closes_generator():
    started = asyncio.Event()

    class _SlowReply:
        def __init__(self) -> None:
            self.closed = False

        async def stream(self, ctx):
            started.set()
            try:
                await asyncio.sleep(10)  # block so we can cancel mid-stream
                yield "never"
            finally:
                self.closed = True

    reply = _SlowReply()
    svc = _make_service(reply)
    task = asyncio.create_task(svc._process_context(_ctx([{"role": "user", "content": "hi"}])))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert reply.closed is True
    # the end frame must still have been pushed (finally), and start must have preceded it
    assert any(isinstance(f, LLMFullResponseStartFrame) for f in svc._pushed)  # type: ignore[attr-defined]
    assert any(isinstance(f, LLMFullResponseEndFrame) for f in svc._pushed)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_bargein_cancel_does_not_speak_fallback():
    # Live-session bug: a barge-in BEFORE the first token cancelled the stream with
    # emitted_any=False, and the finally block then spoke the canned probe ("Could you walk me
    # through a specific example of that?") right after the student had taken the floor — 4 of
    # 4 'fallbacks' in the live session were this, not real degradations. A cancelled turn must
    # stay silent.
    started = asyncio.Event()

    class _SlowReply:
        async def stream(self, ctx):
            started.set()
            await asyncio.sleep(10)  # cancelled before the first token
            yield "never"

    svc = ReplyGeneratorLLMService(_SlowReply(), fallback_text=lambda: "FALLBACK")
    pushed = _capture_via_make(svc)
    task = asyncio.create_task(svc._process_context(_ctx([{"role": "user", "content": "hi"}])))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    texts = [f.text for f in pushed if isinstance(f, LLMTextFrame)]
    assert texts == []  # no fallback spoken on cancellation
