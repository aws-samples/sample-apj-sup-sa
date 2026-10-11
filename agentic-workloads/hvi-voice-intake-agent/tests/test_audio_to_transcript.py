#!/usr/bin/env python3
"""Drive real audio through the real pipeline and assert a transcript comes out.

WHY THIS EXISTS
A live call produced stt={'turns': 0, 'arbitrated': 0, 'empty': 0} over 63 seconds
while the caller was speaking. All three counters at zero is specific: turns is
incremented at the top of run_stt and empty only after it, so zero for both means
run_stt was never entered at all - not that transcription was attempted and failed.

Reading Pipecat 1.7.0 establishes that run_stt has exactly one trigger:
SegmentedSTTService._handle_user_stopped_speaking, reached only from a
VADUserStoppedSpeakingFrame. So the question is narrow: does a
VADUserStoppedSpeakingFrame produced by VADProcessor actually arrive at the STT
service in THIS pipeline? Static reading says it should - VADProcessor broadcasts
that exact class and SegmentedSTTService.process_frame matches on it - and
tests/diag_vad.py separately proves the analyser itself fires on this audio, with
enormous headroom (it still fires attenuated 100x).

Everything therefore checks out in isolation, which is precisely when an
end-to-end assertion earns its keep. tests/test_media_pipeline.py builds this
pipeline and exercises the OUTPUT path, but never pushes audio in and never runs
the task, so a break anywhere along input -> VAD -> STT is invisible to it. That
gap is why this shipped.

WHAT IS REAL AND WHAT IS NOT
Real: AudioSocketInputTransport, VADProcessor, SileroVADAnalyzer with the live
VAD_PARAMS, SegmentedSTTService's buffering and triggering, the PipelineTask
runner, and real 8kHz speech.

Stubbed: only the transcription call itself, so this needs no AWS and no GPU. The
stub subclasses HybridSTTService and overrides run_stt, which means the inherited
base-class machinery under test is untouched.

So a failure here localises to the audio path or the VAD/STT handshake, and a pass
clears them - pointing instead at the audio actually arriving on the socket, which
the inbound-rate windows in media/audiosocket.py report on a live call.

Run:  ./.venv/bin/python tests/test_audio_to_transcript.py
"""
from __future__ import annotations

import asyncio
import glob
import os
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HVI_TTS", "omnivoice")
os.environ.setdefault("OMNIVOICE_ENDPOINT", "hvi-omnivoice")
os.environ.setdefault("AGENT_RUNTIME_ARN",
                      "arn:aws:bedrock-agentcore:ap-south-1:0:runtime/test")

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def load_speech() -> tuple[bytes, int]:
    """Real 8kHz speech. The agent's own TTS output is what exists offline."""
    for path in sorted(glob.glob("build/omnivoice/samples/*.wav")):
        with wave.open(path, "rb") as w:
            if w.getframerate() == 8000 and w.getsampwidth() == 2 and w.getnchannels() == 1:
                return w.readframes(w.getnframes()), 8000
    raise SystemExit(
        "no 8kHz mono 16-bit sample found under build/omnivoice/samples - "
        "run tools/render_samples.py first")


async def main() -> int:
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.frames.frames import Frame, TranscriptionFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.audio.vad_processor import VADProcessor
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    from media.audiosocket import SAMPLE_RATE, AudioSocketTransport
    from media.bot import VAD_PARAMS
    from media.stt import HybridSTTService

    speech, rate = load_speech()
    check("loaded real 8kHz speech", rate == SAMPLE_RATE,
          f"{len(speech)} bytes = {len(speech) / 2 / rate:.1f}s at {rate}Hz")

    # --------------------------------------------------------------------- #
    # STT with only the network call replaced.
    # --------------------------------------------------------------------- #
    class StubSTT(HybridSTTService):
        """Counts run_stt entries. Everything inherited stays real."""

        def __init__(self, **kw):
            super().__init__(**kw)
            self.calls: list[int] = []
            self.first_bytes: list[bytes] = []

        async def run_stt(self, audio: bytes):
            self.calls.append(len(audio))
            self.first_bytes.append(audio[:4])
            self._stats["turns"] += 1
            yield TranscriptionFrame(
                text="हिंदी", user_id="caller", timestamp="2026-01-01T00:00:00Z",
                language=None,
            )

    class Sink(FrameProcessor):
        """Collects transcripts at the end of the pipeline."""

        def __init__(self):
            super().__init__()
            self.transcripts: list[str] = []

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if isinstance(frame, TranscriptionFrame):
                self.transcripts.append(frame.text)
            await self.push_frame(frame, direction)

    transport = AudioSocketTransport()
    stt = StubSTT()
    sink = Sink()
    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(sample_rate=SAMPLE_RATE, params=VAD_PARAMS),
    )

    # Deliberately the same order as media/bot.py, minus the agent and TTS, which
    # would need AWS. The segment under test is input -> vad -> stt.
    pipeline = Pipeline([transport.input(), vad, stt, sink])
    task = PipelineTask(
        pipeline,
        params=PipelineParams(audio_in_sample_rate=SAMPLE_RATE,
                              audio_out_sample_rate=SAMPLE_RATE),
        conversation_id="test-audio-to-transcript",
        idle_timeout_secs=30.0,
    )

    runner = PipelineRunner(handle_sigint=False)
    run = asyncio.create_task(runner.run(task))

    # Let StartFrame propagate. Without it the input transport is not ready and
    # feed() silently discards - which is itself a failure mode worth not
    # accidentally testing instead of the one we care about.
    await asyncio.sleep(2.0)
    check("input transport became ready", transport.audio_in._ready,  # noqa: SLF001
          "set by AudioSocketInputTransport.start via set_transport_ready")

    # ------------------------------------------------------------------ #
    # Feed speech, then silence.
    #
    # The trailing silence is not padding - it is the trigger. VAD only emits
    # VADUserStoppedSpeakingFrame after stop_secs of non-speech, and that frame
    # is the sole thing that invokes run_stt. Speech alone would buffer forever
    # and transcribe nothing, which would look exactly like the live failure.
    # ------------------------------------------------------------------ #
    chunk = SAMPLE_RATE * 2 * 20 // 1000  # 20ms, as Asterisk delivers
    for off in range(0, len(speech) - chunk + 1, chunk):
        await transport.audio_in.feed(speech[off:off + chunk])
        await asyncio.sleep(0)  # let the pipeline drain; faster than realtime

    silence_s = VAD_PARAMS.stop_secs + 1.0
    for _ in range(int(silence_s * 1000 / 20)):
        await transport.audio_in.feed(b"\x00" * chunk)
        await asyncio.sleep(0)

    # VAD and STT hand off through queues and a thread executor, so give them a
    # moment of real time to settle rather than asserting into a race.
    for _ in range(50):
        if stt.calls:
            break
        await asyncio.sleep(0.1)

    check("VAD triggered STT at least once", bool(stt.calls),
          f"run_stt entered {len(stt.calls)} time(s)")
    if stt.calls:
        secs = (stt.calls[0] - 44) / 2 / SAMPLE_RATE  # minus WAV header
        check("segment carries a plausible amount of audio", secs > 0.3,
              f"first segment {secs:.2f}s")
        # WHAT FORMAT DOES run_stt ACTUALLY RECEIVE?
        #
        # This matters because HybridSTTService.run_stt calls pcm_to_wav() on its
        # argument. That is correct only if the argument is raw PCM. The base class
        # decides, via wants_wav_segments, and it defaults to True - meaning it
        # hands over a finished WAV. Wrapping a WAV in a second WAV puts the inner
        # 44-byte RIFF header where the first phoneme should be and overstates the
        # declared data length by 44 bytes.
        #
        # Asserting the raw fact rather than the intent, so the mismatch is visible
        # either way round.
        got = stt.first_bytes[0]
        check("base class hands run_stt RAW PCM, not a WAV "
              "(pcm_to_wav in run_stt would otherwise double-wrap)",
              got != b"RIFF", f"first 4 bytes = {got!r}")

    for _ in range(30):
        if sink.transcripts:
            break
        await asyncio.sleep(0.1)
    check("transcript reached the end of the pipeline", bool(sink.transcripts),
          repr(sink.transcripts[:3]))

    await task.cancel()
    try:
        await asyncio.wait_for(run, timeout=10)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    print()
    print("=" * 74)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print("  -", f)
        print()
        print("If 'VAD triggered STT' failed, the live zero-transcript bug is "
              "REPRODUCED offline and is in the input/VAD/STT path.")
        print("If everything passed, that path is sound and the fault is in the "
              "audio arriving from Asterisk - check the inbound rate windows.")
        return 1
    print("Audio in produces a transcript out. The VAD/STT handshake is sound, so "
          "a live call seeing zero transcripts means the audio never arrived.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
