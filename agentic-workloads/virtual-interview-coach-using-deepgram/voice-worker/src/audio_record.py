"""Consent-gated per-turn audio recording (F006 / G6) — buffer PCM, async upload to S3 SSE-KMS.

The live loop already receives inbound student PCM and outbound TTS PCM as 16kHz mono linear16 (s16).
When a session is recorded (consent on), this module accumulates each turn's PCM into an in-memory
buffer and, AFTER the turn, uploads it asynchronously to S3 under SSE-KMS, returning the uri to be
written into conversation_turn.audio_uri. Everything here is OFF the response_gap clock by construction
(Constitution I / SC-003): appending bytes is a cheap memory copy, and the S3 PUT is awaited on a
separate task scheduled after the turn's reply is already streaming.

Privacy (Constitution III): audio lives ONLY in S3 (SSE-KMS, single region) + the uri in RDS — never in
AgentCore, never in logs. This module logs only counts/ids; never raw bytes, never the uri body. When
AUDIO_BUCKET is unconfigured (local/dev), upload is a no-op returning None, mirroring resume_store.
"""

from __future__ import annotations

import io
import logging
import wave

from .config import Config

log = logging.getLogger("voice_worker")

_SAMPLE_RATE = 16000  # inbound + TTS PCM are both 16kHz mono linear16 (pipecat_pipeline._SAMPLE_RATE)
_SAMPLE_WIDTH = 2     # s16 = 2 bytes/sample
_CHANNELS = 1


class TurnAudioBuffer:
    """Accumulates one turn's PCM frames. append() is a cheap memory copy on frames the loop already
    handles; take() drains and resets for the next turn."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def append(self, pcm: bytes) -> None:
        if pcm:
            self._chunks.append(pcm)

    def take(self) -> bytes:
        data = b"".join(self._chunks)
        self._chunks = []
        return data

    def __len__(self) -> int:
        return sum(len(c) for c in self._chunks)


def _wav_bytes(pcm: bytes) -> bytes:
    """Wrap raw 16kHz mono s16 PCM in a WAV container (so the object is self-describing for playback)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(_CHANNELS)
        w.setsampwidth(_SAMPLE_WIDTH)
        w.setframerate(_SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def audio_key(session_id: str, turn_id: str) -> str:
    """Per-turn object key: audio/{session_id}/{turn_id}.wav (session prefix enables race-safe delete)."""
    return f"audio/{session_id}/{turn_id}.wav"


async def upload_turn_audio(
    config: Config, session_id: str, turn_id: str, pcm: bytes
) -> str | None:
    """Encode the turn's PCM as WAV and PUT it to S3 under SSE-KMS. Returns the s3:// uri, or None on
    failure / unconfigured bucket / empty audio. NEVER raises into the live loop; NEVER logs raw bytes
    or the uri body.

    The boto3 PUT is synchronous, so it is run in a thread executor to avoid blocking the event loop —
    and it is only ever awaited on a task scheduled AFTER the turn, off the response_gap clock."""
    if not config.record_audio or not config.audio_bucket or not pcm:
        return None
    try:
        import asyncio

        wav = _wav_bytes(pcm)
        key = audio_key(session_id, turn_id)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _put_object, config, key, wav)
        uri = f"s3://{config.audio_bucket}/{key}"
        log.info("session %s recorded turn audio (%d bytes) -> object stored", session_id, len(wav))
        return uri
    except Exception as exc:  # noqa: BLE001 - a recording failure must never affect the session
        log.warning("session %s turn-audio upload failed (%s); turn left without audio",
                    session_id, type(exc).__name__)
        return None


def _put_object(config: Config, key: str, body: bytes) -> None:
    import boto3
    from botocore.config import Config as BotoConfig

    # Bounded connect/read timeouts + capped retries so a hung S3 PUT fails fast instead of blocking
    # the executor thread forever (cancelling the asyncio task does NOT unblock the underlying thread —
    # the cap must live at the network layer; code-review finding #10).
    boto_cfg = BotoConfig(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2, "mode": "standard"})
    client = boto3.client("s3", region_name=config.aws_region, config=boto_cfg)
    extra = {"ServerSideEncryption": "aws:kms", "ContentType": "audio/wav"}
    if config.audio_kms_key_id:
        extra["SSEKMSKeyId"] = config.audio_kms_key_id
    client.put_object(Bucket=config.audio_bucket, Key=key, Body=body, **extra)
