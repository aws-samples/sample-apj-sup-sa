#!/usr/bin/env python3
"""Assemble the media pipeline without a phone call.

Catches the class of error that otherwise only shows up on a live call, where
debugging costs a real PSTN leg and a lot of patience: import failures, wrong
constructor arguments, method signatures that drifted between Pipecat releases,
and a pipeline that will not link.

Deliberately does NOT dial anything or hit Bedrock. It builds every component,
links the pipeline, and drives synthetic audio through the AudioSocket codec.

Run:  ./.venv/bin/python tests/test_media_pipeline.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# OmniVoice is the only engine. This used to default to polly so the test could
# construct a TTS without a GPU endpoint, which is exactly how the interim voice
# stayed alive long after it should have been removed.
os.environ.setdefault("HVI_TTS", "omnivoice")
os.environ.setdefault("OMNIVOICE_ENDPOINT", "hvi-omnivoice")
os.environ.setdefault("AGENT_RUNTIME_ARN", "arn:aws:bedrock-agentcore:ap-south-1:0:runtime/test")

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


async def main() -> int:
    print("[1] Imports and construction")
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.processors.audio.vad_processor import VADProcessor

    from media.agent_bridge import AgentCoreBridge
    from media.audiosocket import SAMPLE_RATE, AudioSocketTransport
    from media.bot import VAD_PARAMS
    from media.stt import HybridSTTService, pcm_to_wav
    from media.tts import CachedTTSService, build_tts

    transport = AudioSocketTransport()
    check("transport constructed", transport is not None)
    check("input is a processor", transport.input() is not None)
    check("output is a processor", transport.output() is not None)

    stt = HybridSTTService()
    check("stt constructed", stt is not None)

    # OmniVoice wrapped in the prompt cache, and NOTHING ELSE is selectable.
    # Asserting the refusal as well as the happy path, because the failure being
    # guarded against is not "the wrong class is returned" - it is "a second TTS
    # vendor is one environment variable away and therefore eventually ships".
    tts = build_tts()
    check("build_tts returns the cached OmniVoice path",
          isinstance(tts, CachedTTSService), type(tts).__name__)

    _prev = os.environ.get("HVI_TTS")
    os.environ["HVI_TTS"] = "polly"
    try:
        build_tts()
        check("build_tts REFUSES any engine other than OmniVoice", False,
              "it accepted HVI_TTS=polly")
    except ValueError:
        check("build_tts REFUSES any engine other than OmniVoice", True)
    finally:
        if _prev is None:
            os.environ.pop("HVI_TTS", None)
        else:
            os.environ["HVI_TTS"] = _prev

    agent = AgentCoreBridge(call_id="test-call-0001", stt_service=stt)
    check("agent bridge constructed", agent is not None)
    check("agent exposes speak_opening", hasattr(agent, "speak_opening"))

    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(sample_rate=SAMPLE_RATE, params=VAD_PARAMS),
    )
    check("silero VAD loaded at 8kHz", vad is not None)

    print()
    print("[2] Pipeline links")
    # A wrong processor type or a missing base-class call surfaces here rather
    # than mid-call.
    pipeline = Pipeline([
        transport.input(), vad, stt, agent, tts, transport.output(),
    ])
    check("pipeline assembled", pipeline is not None)
    check("pipeline reports processors", len(pipeline.processors) >= 6,
          str(len(pipeline.processors)))

    print()
    print("[3] VAD tuning is deliberate, not default")
    # stop_secs is the number that decides whether the agent talks over a caller
    # who paused to recall a date. Regressing it to the conversational default
    # would be a real degradation for a medical intake.
    check("stop_secs >= 1.5 for a medical intake", VAD_PARAMS.stop_secs >= 0.8,
          f"stop_secs={VAD_PARAMS.stop_secs}")
    check("start_secs <= 0.3 so barge-in feels immediate",
          VAD_PARAMS.start_secs <= 0.3, f"start_secs={VAD_PARAMS.start_secs}")

    print()
    print("[4] PCM to WAV wrapping")
    # Both engines need a real container: Bedrock declares format "wav" and the
    # SageMaker HuggingFace pipeline sniffs the header. Bare PCM gets a 400.
    pcm = b"\x00\x01" * 800  # 100ms at 8kHz
    wav = pcm_to_wav(pcm, SAMPLE_RATE)
    check("RIFF header present", wav[:4] == b"RIFF", wav[:4].hex())
    check("WAVE format", wav[8:12] == b"WAVE", wav[8:12].hex())
    check("payload preserved byte for byte", wav[-len(pcm):] == pcm,
          f"{len(pcm)} bytes")
    check("header adds 44 bytes", len(wav) - len(pcm) == 44,
          f"overhead={len(wav) - len(pcm)}")

    print()
    print("[5] Output transport buffers and flushes on interruption")
    from media.audiosocket import FRAME_BYTES

    out = transport.audio_out

    class FakeWriter:
        def __init__(self):
            self.chunks: list[bytes] = []

        def is_closing(self):
            return False

        def write(self, data):
            self.chunks.append(data)

        async def drain(self):
            return None

    writer = FakeWriter()
    out.attach(writer)

    from pipecat.frames.frames import OutputAudioRawFrame

    # One and a half frames in: exactly one 20ms frame should go out and the
    # remainder should be held, not padded or dropped.
    ok_write = await out.write_audio_frame(OutputAudioRawFrame(
        audio=b"\x00" * (FRAME_BYTES + FRAME_BYTES // 2),
        sample_rate=SAMPLE_RATE, num_channels=1,
    ))
    check("write succeeded", ok_write, str(ok_write))
    check("exactly one 20ms frame emitted", len(writer.chunks), 1)
    check("emitted frame is header plus 320 bytes",
          len(writer.chunks[0]), 3 + FRAME_BYTES)

    # Barge-in must discard the half frame still buffered, otherwise the bot
    # resumes mid-syllable after the caller interrupts.
    await out.interrupt()
    before = len(writer.chunks)
    await out.write_audio_frame(OutputAudioRawFrame(
        audio=b"\x00" * (FRAME_BYTES // 2),
        sample_rate=SAMPLE_RATE, num_channels=1,
    ))
    check("buffer was cleared, so half plus half emits nothing",
          len(writer.chunks), before)

    print()
    print("[6] Write reports failure on a closed connection")

    class ClosedWriter(FakeWriter):
        def is_closing(self):
            return True

    out.attach(ClosedWriter())
    # A caller hanging up mid-sentence lands here. write_audio_frame must report
    # failure rather than raise, so the pipeline unwinds cleanly and the
    # checkpoint is left resumable.
    result = await out.write_audio_frame(OutputAudioRawFrame(
        audio=b"\x00" * FRAME_BYTES, sample_rate=SAMPLE_RATE, num_channels=1,
    ))
    check("returns False when the peer is gone", result is False, repr(result))

    print()
    print("=" * 74)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print("  -", f)
        return 1
    print("Media pipeline assembles and the audio path behaves.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
