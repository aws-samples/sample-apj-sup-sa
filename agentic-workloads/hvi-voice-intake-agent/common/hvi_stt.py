"""
Speech-to-text: a single Whisper call per turn.

Whisper large-v3 on SageMaker is the one engine. It was measured perfect on all
three modes of the 8kHz telephony fixtures (English, Hindi, genuine Hinglish
code-mixing) and never translates - its Hindi output was Devanagari on every
fixture (tests/wer_harness.py, tests/test_stt.py).

Every turn is transcribed exactly once. There is no second opinion, no
escalation, no prompt biasing and no translation/repair path: `_whisper` sends
raw audio bytes to the endpoint and returns the transcript. The earlier hybrid
cascade (Voxtral primary with a script-mismatch / plausibility escalation and a
Whisper backstop) has been removed - along with the `arbitrated: 0` bug that
cascade carried, which is gone because the broken escalation trigger is gone.

`transcribe` never raises: a failed transcription comes back empty so the
orchestrator re-asks rather than dropping the call.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import wave

import boto3
from botocore.config import Config

log = logging.getLogger("hvi.stt")

DATA_REGION = os.environ.get("DATA_REGION", "ap-south-1")

WHISPER_ENDPOINT = os.environ.get("STT_ENDPOINT", "hvi-whisper")

# Minimum audio worth sending. Below this it is almost always a false trigger on
# silence or line noise.
MIN_AUDIO_SECONDS = float(os.environ.get("STT_MIN_SECONDS", "0.35"))

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_LATIN = re.compile(r"[A-Za-z]")

_sagemaker = boto3.client(
    "sagemaker-runtime",
    region_name=DATA_REGION,
    config=Config(read_timeout=25, connect_timeout=5, retries={"max_attempts": 1}),
)


# --------------------------------------------------------------------------- #
# audio helpers
# --------------------------------------------------------------------------- #
def audio_duration(wav_bytes: bytes) -> float:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 8000)
    except Exception:
        # Not a parseable WAV container; assume 8kHz 16-bit mono PCM.
        return len(wav_bytes) / 2 / 8000


def script_of(text: str) -> str:
    """Dominant script. Mirrors the harness so measurement and runtime agree."""
    dev = len(_DEVANAGARI.findall(text))
    lat = len(_LATIN.findall(text))
    if dev and lat:
        # Some of the other script is expected - English medical terms sit
        # inside Hindi sentences. Only call it mixed when both are substantial.
        ratio = min(dev, lat) / max(dev, lat)
        return "mixed" if ratio > 0.15 else ("deva" if dev > lat else "latin")
    if dev:
        return "deva"
    if lat:
        return "latin"
    return "none"


# --------------------------------------------------------------------------- #
# engine
# --------------------------------------------------------------------------- #
def _whisper(wav_bytes: bytes, endpoint: str | None = None) -> str:
    """
    Whisper large-v3 on SageMaker.

    ContentType MUST be audio/x-audio. audio/wav, audio/x-wav,
    application/octet-stream and the JSON+base64 form all return 400
    InternalServerException from the HuggingFace inference toolkit - verified in
    tests/diag_whisper_invoke.py.
    """
    resp = _sagemaker.invoke_endpoint(
        EndpointName=endpoint or WHISPER_ENDPOINT,
        ContentType="audio/x-audio",
        Accept="application/json",
        Body=wav_bytes,
    )
    payload = json.loads(resp["Body"].read())
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if isinstance(payload, dict):
        return (payload.get("text") or "").strip()
    return str(payload).strip()


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #
def transcribe(
    wav_bytes: bytes,
    expected_language: str | None = None,
    engine: str | None = None,
    expected_answer_type: str | None = None,
    endpoint: str | None = None,
) -> dict:
    """
    Transcribe one utterance with Whisper.

    `expected_language`, `engine` and `expected_answer_type` are retained for
    call-site compatibility and are ignored: there is no script-mismatch guard,
    no engine routing and no answer-type prompt biasing any more. `endpoint`
    overrides the default Whisper endpoint (used by lambdas/sma/handler.py).

    Returns {text, engine, stages, durationSeconds, script, arbitrated, empty}
    (+ `error` on failure, + `reason` on the too-short short-circuit). `engine`
    is always "whisper" (except the too-short short-circuit, which keeps "none").
    `arbitrated` is hardcoded False: arbitration no longer exists, but
    media/stt.py reads the key for its stats counter, so it is kept rather than
    dropped. Never raises.
    """
    secs = audio_duration(wav_bytes)
    base = {"durationSeconds": round(secs, 2), "arbitrated": False,
            "stages": ["whisper"]}

    if secs < MIN_AUDIO_SECONDS:
        return {**base, "text": "", "engine": "none", "stages": [],
                "script": "none", "empty": True,
                "reason": "too short to be speech"}

    try:
        text = _whisper(wav_bytes, endpoint)
        return {**base, "text": text, "engine": "whisper",
                "script": script_of(text), "empty": not text}
    except Exception as e:
        log.warning("whisper failed: %s", e)
        return {**base, "text": "", "engine": "whisper", "empty": True,
                "script": "none", "error": str(e)}
