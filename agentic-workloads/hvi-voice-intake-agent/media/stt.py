"""Pipecat STT service over the Whisper engine (common/hvi_stt.py).

SegmentedSTTService is the right base class rather than STTService: the engine
is HTTP request/response, not a streaming websocket. Pipecat's local VAD chunks
the caller's speech, hands us a complete utterance, and we POST it. That also
means barge-in detection does NOT depend on the STT - Silero VAD upstream fires
the moment the caller speaks, long before any transcript exists.

The orchestrator still pushes the current session language and expected answer
type down into this service as it tracks them across the call. They are retained
as metadata only: Whisper transcribes raw audio with no language or answer-type
biasing, so they no longer influence transcription.
"""
from __future__ import annotations

import io
import logging
import os
import sys
import time
import wave
from collections.abc import AsyncGenerator

from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.stt_service import SegmentedSTTService

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))
import hvi_stt  # noqa: E402

log = logging.getLogger("hvi.media.stt")


def pcm_to_wav(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
    """Wrap raw PCM in a WAV container.

    Both engines want a real container: Bedrock's Converse audio block is
    declared as format "wav", and the SageMaker HuggingFace pipeline sniffs the
    header. Handing either one bare PCM produces an opaque 400.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)  # signed 16-bit
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


# What a segment is scaled up to before transcription. Chosen from measurement, not
# taste: the 8kHz clips this engine transcribes correctly sit at RMS ~4800, and
# audio arriving from the phone line measured RMS ~500 average across a whole call
# (peaks 524 to 16764). About ten times quieter than the level the model does well
# on.
TARGET_RMS = float(os.environ.get("STT_TARGET_RMS", "3500"))

# Ceiling for the loudest sample after scaling, ~0.88 of full scale. Gain is capped
# so this is not exceeded, because clipping invents harmonics that were never
# spoken and would trade one transcription problem for a worse one.
PEAK_CEILING = 29000


def normalise_gain(pcm: bytes) -> bytes:
    """Scale a segment towards TARGET_RMS without clipping it.

    WHY QUIET AUDIO NEEDED FIXING SEPARATELY
    Removing the double-WAV click measurably improved transcription but did not
    solve it: on the next live call long utterances came through verbatim
    ("Hypertension, cholesterol, blood pressure, etc.") while short ones did not
    ("Um.", "Why coding?", "White Golden.", "My code.", "Hi, Golden." - five
    attempts at one medication name).

    That split is the diagnostic. A long utterance gives the model enough context to
    recover from a poor signal-to-noise ratio; a one or two word answer gives it
    none, so the same quiet audio that is merely survivable in a sentence becomes
    unintelligible on its own. And short answers are most of a yes/no medical
    questionnaire.

    It also stops being only an accuracy problem. "White Golden." was handed to the
    condition classifier, which confidently returned Chronic Kidney Disease and
    asked the caller to confirm a disease they had never mentioned. Garbage in a
    transcript does not stay in the transcript.

    Deliberately RMS-based with a peak cap rather than peak normalisation: peak
    normalisation is decided entirely by the single loudest sample, so one cough or
    line click sets the gain for the whole utterance and quiet speech stays quiet.

    Returns the input unchanged when it is already loud enough or is silent - there
    is nothing to be gained from amplifying a segment that holds no speech, and
    scaling near-silence would just raise the noise floor into the model.
    """
    if not pcm:
        return pcm
    try:
        import numpy as np

        samples = np.frombuffer(pcm, dtype=np.int16)
        if samples.size == 0:
            return pcm
        rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
        # Below this there is no speech to rescue, only noise to amplify. The floor
        # is under the level at which VAD fires at all (measured ~49), so anything
        # the VAD passed through is above it.
        if rms < 20 or rms >= TARGET_RMS:
            return pcm
        peak = int(np.abs(samples).max()) or 1
        gain = min(TARGET_RMS / rms, PEAK_CEILING / peak)
        if gain <= 1.01:
            return pcm
        scaled = np.clip(samples.astype(np.float64) * gain,
                         -32768, 32767).astype(np.int16)
        log.debug("normalised segment gain x%.2f (rms %.0f -> %.0f, peak %d)",
                  gain, rms, rms * gain, int(peak * gain))
        return scaled.tobytes()
    except Exception as exc:  # noqa: BLE001 - never lose a turn over gain
        log.warning("gain normalisation skipped: %s", exc)
        return pcm


class HybridSTTService(SegmentedSTTService):
    """Transcribes each VAD-delimited utterance with Whisper.

    Measured perfect slot recall on 24 telephony-band fixtures (English, Hindi,
    Hinglish). The class name is retained for compatibility with existing callers
    and tests; the engine is a single Whisper call per turn.
    """

    def __init__(self, *, engine: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self._engine = engine or os.environ.get("STT_ENGINE", "hybrid")
        # Seeded from the form's default and then kept in step with the
        # orchestrator, which owns language detection for the call.
        self._expected_language = os.environ.get("HVI_DEFAULT_LANGUAGE", "hinglish")
        # None until the orchestrator says otherwise, which means free text and no
        # plausibility gating - the safe default.
        self._expected_answer_type: str | None = None
        self._stats = {"turns": 0, "arbitrated": 0, "empty": 0, "lowConfidence": 0}

    @property
    def wants_wav_segments(self) -> bool:
        """False: hand run_stt RAW PCM, because run_stt does its own wrapping.

        THIS WAS CORRUPTING THE START OF EVERY UTTERANCE.

        SegmentedSTTService defaults this to True, which means it wraps the VAD
        segment in a WAV container before calling run_stt. But run_stt already
        calls pcm_to_wav() on what it is given, because both engines need a real
        container. The two together wrapped a WAV inside a second WAV.

        That is not a harmless framing quirk. The inner 44-byte RIFF header ends up
        where the first samples of speech should be, and read as signed 16-bit it is
        not quiet: "RI" is 18770 and "FF" is 17990, well over half of full scale. So
        every segment opened with roughly 22 samples of loud broadband click,
        immediately before the first phoneme, and the outer header then overstated
        the data length by 44 bytes.

        Measured effect on a live call: single-word answers were the worst hit,
        because the click is a larger fraction of a short segment. "Hindi" came back
        as "Indeed.", and several turns produced "Huh?" and "Hi." from real speech.
        A transient at onset is exactly what damages the first phoneme and the
        language identification that depends on it.

        Returning False makes the base class hand over the unwrapped buffer, which is
        what run_stt has always assumed it was getting. Verified by
        tests/test_audio_to_transcript.py, which asserts the argument does not start
        with b"RIFF" - the check that caught this.
        """
        return False

    def set_expected_answer_type(self, answer_type: str | None) -> None:
        """What shape of answer the pending question wants, from the orchestrator.

        Retained because the agent bridge sets it per turn. STT is Whisper-only, so
        this is recorded but no longer influences transcription - there is no
        answer-type plausibility gate or second opinion any more.
        """
        if answer_type != self._expected_answer_type:
            log.debug("stt expected answer type %s -> %s",
                      self._expected_answer_type, answer_type)
            self._expected_answer_type = answer_type

    def set_expected_language(self, language: str) -> None:
        """Told to us by the agent bridge when the caller switches language."""
        if language and language != self._expected_language:
            log.info("stt expected language %s -> %s", self._expected_language, language)
            self._expected_language = language

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        """Transcribe one VAD-delimited utterance."""
        started = time.perf_counter()
        wav = pcm_to_wav(normalise_gain(audio), self.sample_rate)

        await self.start_ttfb_metrics()
        try:
            result = hvi_stt.transcribe(
                wav,
                expected_language=self._expected_language,
                engine=self._engine,
                expected_answer_type=self._expected_answer_type,
            )
        except Exception as exc:  # noqa: BLE001 - never kill the call on STT
            log.exception("stt failed")
            yield ErrorFrame(f"transcription failed: {exc}")
            return
        finally:
            await self.stop_ttfb_metrics()

        self._stats["turns"] += 1
        if result.get("arbitrated"):
            self._stats["arbitrated"] += 1
        if result.get("lowConfidence"):
            # Both engines produced something that cannot answer the question. Worth
            # counting separately: a call full of these is a line or microphone
            # problem, not a model problem, and the two need different responses.
            self._stats["lowConfidence"] += 1
        elapsed = int((time.perf_counter() - started) * 1000)

        text = (result.get("text") or "").strip()
        if not text:
            # Silence or line noise. Emit nothing: the orchestrator's re-ask path
            # is driven by the absence of an answer, and pushing an empty
            # transcript would burn one of the caller's three attempts.
            self._stats["empty"] += 1
            log.debug("empty transcript after %dms (%s)", elapsed, result.get("reason"))
            return

        log.info("stt %dms via %s (%s) script=%s: %s",
                 elapsed, result.get("engine"), "+".join(result.get("stages") or []),
                 result.get("script"), text[:80])

        yield TranscriptionFrame(
            text=text,
            user_id="caller",
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            # Left as None deliberately. Pipecat's Language enum has no notion of
            # Hinglish code-mixing, and forcing it to hi-IN or en-IN would
            # mislabel the majority of real utterances. The orchestrator carries
            # the language of record.
            language=None,
            result=result,
        )
