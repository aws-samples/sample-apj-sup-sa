"""Unit tests for AnalyticsAgentCoreProcessor turn lifecycle.

These are offline (no AWS, no network): they stub the aiohttp call and the Pipecat
frame plumbing, and assert the behaviours that fix the voice-turn-hang bug:

1. The spoken filler is OFF by default (it caused a false-barge-in echo loop on
   transports without acoustic echo cancellation). It only emits when
   VOICE_SPOKEN_FILLER=true.
2. A normal turn streams the agent answer and closes the LLM response
   (LLMFullResponseStart → LLMTextFrame → LLMFullResponseEnd).
3. A cancellation mid-stream (a real barge-in) re-raises CancelledError so Pipecat
   can tear the turn down — it is never swallowed (swallowing wedged the pipeline).
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipecat.frames.frames import (  # noqa: E402
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

import analytics_processor as ap  # noqa: E402


# ── Test doubles ──────────────────────────────────────────────────────────────
class _FakeResp:
    """Minimal aiohttp response stand-in yielding a fixed SSE byte script."""

    def __init__(self, chunks, status=200, content_type="text/event-stream", stall=False):
        self.status = status
        self.headers = {"Content-Type": content_type}
        self._chunks = chunks
        self._stall = stall

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def text(self):
        return ""

    @property
    def content(self):
        chunks, stall = self._chunks, self._stall

        class _Body:
            async def iter_any(self_inner):
                for c in chunks:
                    yield c
                if stall:
                    # Emulate a stream that never EOFs (the hang shape) — the caller's
                    # timeout/cancellation must handle it.
                    await asyncio.sleep(3600)

        return _Body()


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def post(self, *a, **k):
        return self._resp


def _make_processor(monkeypatch, chunks, stall=False, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    proc = ap.AnalyticsAgentCoreProcessor(
        agent_arn="arn:aws:bedrock-agentcore:us-west-2:1:runtime/x",
        token_fn=lambda: "demo-token",
        aws_region="us-west-2",
        user_token="header-token",
        session_id="voice-test-session-padpadpadpadpadpad",
        qualifier="agentic_analytics_endpoint",
    )
    # Capture pushed frames; stub the base-class plumbing we don't exercise.
    pushed = []

    async def _push(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    monkeypatch.setattr(proc, "push_frame", _push)
    proc._pushed = pushed

    # Stub the aiohttp client session with our fake.
    resp = _FakeResp(chunks, stall=stall)
    monkeypatch.setattr(ap.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(resp))
    # RTVI display/chart frames import inside methods; let them no-op via the stubbed
    # push_frame (the import will succeed in the test venv).
    return proc


def _sse(text):
    import json

    return ('data: ' + json.dumps(
        {"event": {"contentBlockDelta": {"delta": {"text": text}}}}
    ) + "\n\n").encode()


class _FakeContext:
    pass


def _ctx_frame(monkeypatch, prompt="What are the top five most booked unicorns?"):
    # default_context_to_payload_transformer is called on frame.context; stub it.
    monkeypatch.setattr(
        ap, "default_context_to_payload_transformer",
        lambda ctx: '{"prompt": "%s"}' % prompt,
    )
    f = LLMContextFrame.__new__(LLMContextFrame)
    f.context = _FakeContext()
    return f


# ── Tests ───────────────────────────────────────────────────────────────────
def test_filler_off_by_default(monkeypatch):
    """No TTSSpeakFrame is emitted unless VOICE_SPOKEN_FILLER=true."""
    monkeypatch.delenv("VOICE_SPOKEN_FILLER", raising=False)
    chunks = [_sse("<speak>Top one is Nembus.</speak>"), _sse("Detail line.")]
    proc = _make_processor(monkeypatch, chunks)
    frame = _ctx_frame(monkeypatch)

    asyncio.run(proc.process_frame(frame, FrameDirection.DOWNSTREAM))

    fillers = [f for f in proc._pushed if isinstance(f, TTSSpeakFrame)]
    assert fillers == [], f"expected no spoken filler, got {[f.text for f in fillers]}"
    # The real answer still flows.
    texts = [f.text for f in proc._pushed if isinstance(f, LLMTextFrame)]
    assert any("Nembus" in t for t in texts), texts


def test_filler_on_when_enabled(monkeypatch):
    """VOICE_SPOKEN_FILLER=true + VOICE_DYNAMIC_ACK=false restores the STATIC
    spoken acknowledgement. (The default is now dynamic ack — the model's own
    streamed <ack> — which is asserted separately; the static _FILLERS path only
    fires when dynamic ack is explicitly disabled.)"""
    chunks = [_sse("<speak>Hi.</speak>")]
    proc = _make_processor(monkeypatch, chunks,
                           VOICE_SPOKEN_FILLER="true", VOICE_DYNAMIC_ACK="false")
    frame = _ctx_frame(monkeypatch)

    asyncio.run(proc.process_frame(frame, FrameDirection.DOWNSTREAM))

    fillers = [f for f in proc._pushed if isinstance(f, TTSSpeakFrame)]
    assert len(fillers) == 1, "expected exactly one filler when enabled"


def test_normal_turn_closes_response(monkeypatch):
    """A normal turn emits Start → Text → End (a closed LLM response)."""
    monkeypatch.delenv("VOICE_SPOKEN_FILLER", raising=False)
    chunks = [_sse("<speak>All five are Celestial.</speak>")]
    proc = _make_processor(monkeypatch, chunks)
    frame = _ctx_frame(monkeypatch)

    asyncio.run(proc.process_frame(frame, FrameDirection.DOWNSTREAM))

    kinds = [type(f).__name__ for f in proc._pushed]
    assert "LLMFullResponseStartFrame" in kinds
    assert "LLMFullResponseEndFrame" in kinds
    # End comes after Start.
    assert kinds.index("LLMFullResponseStartFrame") < kinds.index("LLMFullResponseEndFrame")


def test_cancellation_reraises(monkeypatch):
    """A mid-stream cancellation must propagate (never be swallowed)."""
    monkeypatch.delenv("VOICE_SPOKEN_FILLER", raising=False)
    # A stream that never EOFs; we cancel the task while it's blocked.
    chunks = [_sse("<speak>partial")]
    proc = _make_processor(monkeypatch, chunks, stall=True)
    frame = _ctx_frame(monkeypatch)

    async def _run_and_cancel():
        task = asyncio.ensure_future(
            proc.process_frame(frame, FrameDirection.DOWNSTREAM)
        )
        await asyncio.sleep(0.1)
        task.cancel()
        await task  # should raise CancelledError out of process_frame

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_run_and_cancel())


# ── split_presenter_output: never leak <speak> markers into the displayed text ──
# (regression: a malformed/unclosed <speak> showed the literal tag as chat text)
@pytest.mark.parametrize("raw", [
    "<speak>Sure, top one is Nembus.</speak>\nHere is the table.",   # well-formed
    "<speak>Sure, top one is Nembus.\nHere is the table.",           # unclosed open
    "<speak>Sure.</speak>\nDetail </speak> more.",                    # stray close in body
    "Here are your results.\n| a | b |",                             # no speak markers
    "<speak>Hi</speak> mid <speak>again</speak> tail",               # double open
])
def test_split_presenter_never_leaks_speak_markers(raw):
    spoken, displayed = ap.split_presenter_output(raw)
    assert "<speak" not in displayed.lower(), f"leaked open tag: {displayed!r}"
    assert "speak>" not in displayed.lower(), f"leaked close tag: {displayed!r}"
    assert "<speak" not in spoken.lower() and "speak>" not in spoken.lower()


def test_split_presenter_wellformed_drops_ack_from_display():
    """The spoken acknowledgement+headline are NOT echoed verbatim in the display."""
    spoken, displayed = ap.split_presenter_output(
        "<speak>Sure, here's what I found. Nembus leads.</speak>\nThe table follows.")
    assert spoken == "Sure, here's what I found. Nembus leads."
    assert displayed == "The table follows."
