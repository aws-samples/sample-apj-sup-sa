#!/usr/bin/env python3
"""The caller must be able to interrupt the agent.

WHAT WAS WRONG. Barge-in was assumed to be working and was not wired at all. The
VAD is tuned for it (`start_secs` 0.2, commented "so barge-in feels immediate")
and on a live 45-turn call it fired 45 times, so detection was never the problem.
In pipecat 1.7:

    VADProcessor      emits VADUserStartedSpeakingFrame / UserSpeakingFrame
    base_output       acts on InterruptionFrame
    nothing           emits InterruptionFrame in this pipeline

The output transport was listening for a signal nobody sent, so the agent talked
over the caller for the whole of every prompt - up to 16 seconds on the longest
one. There is no `allow_interruptions` flag in 1.7; the wiring is simply absent.

WHY THE POLICY IS TESTED SEPARATELY FROM THE PIPELINE. The dangerous case is not
"does an interruption happen" but "does it happen when it should NOT". This is a
phone bridge and the agent's own audio can return on the inbound leg - an
interruption fires before any transcription, so the echo rejection that protects
the transcript cannot protect this. A bot that interrupts itself cannot finish a
question, which is worse than the dead air being fixed, and it would only show up
on a real call. So the sustained-speech requirement is asserted directly.

Run:  ./.venv/bin/python tests/test_bargein.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipecat.frames.frames import (  # noqa: E402
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    InterruptionFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)

from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

from media import bargein  # noqa: E402
from media.bargein import BargeInProcessor, should_interrupt  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"  <- {detail[:180]}" if not ok and detail else ""))
    if not ok:
        failures.append(label)


def test_policy() -> None:
    print("[1] the caller talking over the agent is an interruption")
    check("sustained speech while the agent talks -> interrupt",
          should_interrupt(speech_started_at=100.0, now=100.5,
                           already_fired=False, bot_speaking=True,
                           seen_tts_frames=True, min_speech_secs=0.4))

    print("\n[2] a BLIP must never interrupt - this is the echo guard")
    # The agent's own audio returning on the inbound leg is loud and short: it
    # stops when the agent stops. A caller who means to interrupt keeps going.
    for elapsed, why in ((0.0, "the instant the VAD fires"),
                         (0.1, "one frame of speech"),
                         (0.39, "just under the threshold")):
        check(f"{elapsed}s of speech does NOT interrupt ({why})",
              not should_interrupt(speech_started_at=100.0, now=100.0 + elapsed,
                                   already_fired=False, bot_speaking=True,
                                   seen_tts_frames=True, min_speech_secs=0.4))

    print("\n[3] it fires once per turn, not once per audio frame")
    check("already fired -> no second interruption",
          not should_interrupt(speech_started_at=100.0, now=101.0,
                               already_fired=True, bot_speaking=True,
                               seen_tts_frames=True, min_speech_secs=0.4))

    print("\n[4] silence is not an interruption")
    check("no speech in progress -> nothing",
          not should_interrupt(speech_started_at=None, now=101.0,
                               already_fired=False, bot_speaking=True,
                               seen_tts_frames=True, min_speech_secs=0.4))

    print("\n[5] an idle agent is not interrupted mid-thought")
    # A caller finishing a long answer while the NEXT question is being
    # synthesised must not cancel that question before it is heard.
    check("agent not speaking, and TTS frames are observable -> no interrupt",
          not should_interrupt(speech_started_at=100.0, now=101.0,
                               already_fired=False, bot_speaking=False,
                               seen_tts_frames=True, min_speech_secs=0.4))
    print("\n[5b] but never seeing TTS frames must not disable barge-in")
    # Not seeing them means this processor cannot observe the agent's speech, not
    # that the agent is silent. The measured fault is that interruption NEVER
    # happened, so this fails open.
    check("no TTS frames observed -> still interrupts",
          should_interrupt(speech_started_at=100.0, now=101.0,
                           already_fired=False, bot_speaking=False,
                           seen_tts_frames=False, min_speech_secs=0.4))

    print("\n[6] the kill switch is honoured")
    original = bargein.ENABLED
    try:
        bargein.ENABLED = False
        check("BARGE_IN_ENABLED=0 -> never interrupts",
              not should_interrupt(speech_started_at=100.0, now=101.0,
                                   already_fired=False, bot_speaking=True,
                                   seen_tts_frames=True, min_speech_secs=0.4))
    finally:
        bargein.ENABLED = original
    check("and it is restored for the rest of this run", bargein.ENABLED is original)


async def drive(frames: list[object], min_secs: float = 0.0) -> list[object]:
    """Run frames through the REAL BargeInProcessor and return what it emitted.

    Only the two outbound calls are captured. The frame handling under test is the
    processor's own - reimplementing it here would make the test agree with itself
    rather than with the code that ships.
    """
    proc = BargeInProcessor(min_speech_secs=min_secs)
    emitted: list[object] = []

    async def capture(frame, direction=None):  # noqa: ANN001
        emitted.append(frame)

    proc.push_frame = capture                   # type: ignore[method-assign]
    proc.broadcast_frame_instance = capture     # type: ignore[method-assign]

    for frame in frames:
        await proc.process_frame(frame, FrameDirection.DOWNSTREAM)
    return emitted


def audio() -> InputAudioRawFrame:
    return InputAudioRawFrame(audio=b"\x00\x00" * 160, sample_rate=8000,
                              num_channels=1)


def test_processor() -> None:
    print("\n[7] end to end through the real processor and real frames")
    emitted = asyncio.run(drive([
        TTSStartedFrame(),                  # the agent starts a long question
        audio(),
        VADUserStartedSpeakingFrame(start_secs=0.2),   # the caller cuts in
        audio(), audio(),
    ]))
    interruptions = [f for f in emitted if isinstance(f, InterruptionFrame)]
    check("an InterruptionFrame is emitted", len(interruptions) == 1,
          f"{len(interruptions)} emitted from {[type(f).__name__ for f in emitted]}")
    check("and every original frame is still forwarded",
          sum(1 for f in emitted if not isinstance(f, InterruptionFrame)) == 5,
          str([type(f).__name__ for f in emitted]))

    print("\n[8] it does not fire again for the same stretch of speech")
    emitted = asyncio.run(drive([
        TTSStartedFrame(),
        VADUserStartedSpeakingFrame(start_secs=0.2),
        audio(), audio(), audio(), audio(),
    ]))
    check("one interruption for one turn",
          len([f for f in emitted if isinstance(f, InterruptionFrame)]) == 1,
          str([type(f).__name__ for f in emitted]))

    print("\n[9] the agent speaking again re-arms it")
    emitted = asyncio.run(drive([
        TTSStartedFrame(),
        VADUserStartedSpeakingFrame(start_secs=0.2), audio(),
        VADUserStoppedSpeakingFrame(),
        TTSStartedFrame(),
        VADUserStartedSpeakingFrame(start_secs=0.2), audio(),
    ]))
    check("two turns, two interruptions",
          len([f for f in emitted if isinstance(f, InterruptionFrame)]) == 2,
          str([type(f).__name__ for f in emitted]))

    print("\n[10] and it stays quiet when the agent is not speaking")
    emitted = asyncio.run(drive([
        TTSStartedFrame(), TTSStoppedFrame(),      # the agent finished
        VADUserStartedSpeakingFrame(start_secs=0.2),
        audio(), audio(),                          # the caller answers normally
    ]))
    check("answering a finished question is not an interruption",
          not [f for f in emitted if isinstance(f, InterruptionFrame)],
          str([type(f).__name__ for f in emitted]))


def test_wiring() -> None:
    print("\n[11] it is actually in the pipeline")
    src = (ROOT / "media" / "bot.py").read_text()
    check("BargeInProcessor is imported", "from media.bargein import BargeInProcessor" in src)
    check("and constructed", "BargeInProcessor()" in src)
    # Read the order out of the Pipeline([...]) list itself. Searching the whole
    # file for "tts," matched `tts_service=tts,` in the bridge construction and
    # made this check pass or fail for the wrong reason.
    block = src.split("Pipeline([", 1)[1].split("])", 1)[0]
    stages = [line.strip().rstrip(",") for line in block.splitlines()
              if line.strip() and not line.strip().startswith("#")]
    # THIS USED TO BE AN EXACT LIST, and a later feature broke it without breaking
    # barge-in: e0a9c29 appended `recorder` to the pipeline so the per-call audio
    # is durable, and this assertion had not been touched since ee050a3. It then
    # failed over a stage that sits downstream of everything barge-in depends on,
    # while all nineteen behavioural checks above still passed - a red suite
    # reporting a problem that did not exist.
    #
    # Pinning the total membership of the pipeline was never this file's job. What
    # it needs to defend is the ORDER of the stages interruption depends on:
    # barge-in must see speech straight out of the VAD, before STT or the agent
    # consume it, and it must sit upstream of the transport it interrupts. So the
    # core order is still exact, and an added stage is required to sit after the
    # output transport rather than anywhere it likes. A reordering still fails;
    # appending no longer does.
    CORE = ["transport.input()", "vad", "barge_in", "stt", "agent", "tts",
            "transport.output()"]
    check("every core stage is present",
          set(CORE) <= set(stages), f"missing {sorted(set(CORE) - set(stages))}")
    check("the pipeline keeps the expected core order",
          [s for s in stages if s in CORE] == CORE, str(stages))
    extra = [s for s in stages if s not in CORE]
    check("any additional stage sits after the output transport",
          all(stages.index(s) > stages.index("transport.output()") for s in extra),
          f"extra={extra} stages={stages}")
    check("barge-in sees speech before the services consume it",
          stages.index("barge_in") == stages.index("vad") + 1, str(stages))
    check("and the output transport is downstream of it",
          stages.index("barge_in") < stages.index("transport.output()"),
          str(stages))


def main() -> int:
    test_policy()
    test_processor()
    test_wiring()
    print()
    if failures:
        print(f"  {len(failures)} of {checks} checks FAILED")
        for f in failures:
            print(f"    - {f}")
        return 1
    print(f"  All {checks} barge-in checks passed.")
    print("  The caller can interrupt, a blip of echo cannot, and an idle agent")
    print("  is not cut off mid-thought.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
