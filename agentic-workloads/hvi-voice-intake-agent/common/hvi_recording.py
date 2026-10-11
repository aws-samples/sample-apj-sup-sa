"""Durable call audio: one WAV per call, written beside that call's transcript.

WHY THIS EXISTS
---------------
The consent the caller gives at A020 says, in their own language, that the call
is being recorded - and until this module existed that was not true. Nothing on
any live path wrote call audio:

  Chime SMA (RecordAudio)   legacy, replaced by Asterisk because an action-based
                            SMA cannot play and listen at once, so barge-in is
                            impossible on it.
  Asterisk / AudioSocket    wrote nothing durable. The HVI_DEBUG_RECORD_INBOUND
                            switch in media/audiosocket.py dumps UNENCRYPTED PCM
                            to an instance volume, which is why it stays off.
  Browser / WebRTC          wrote nothing at all.

So Salesforce's Recording_URI__c was blank on every call, and the one artefact a
disputed medical record most needs - what the caller actually SOUNDED like, not
just our transcription of it - did not exist. A transcript is our interpretation;
the audio is the evidence behind it. When a claims assessor later disputes a
declaration, "the STT heard X" is exactly the claim in question.

WHERE IT GOES, AND WHY NOT A SEPARATE BUCKET
--------------------------------------------
    s3://<CALL_ARCHIVE_BUCKET>/calls/<callId>/audio/call.wav

Deliberately the SAME bucket and prefix as the transcript, rather than the
AUDIO_BUCKET that config/env.sh describes. That bucket was named to match an IAM
wildcard and has never existed in the account; provisioning it would mean a second
bucket to secure, a second lifecycle policy to get right, and a cross-bucket copy
step in hvi_archive - all so the two halves of one call's evidence could live
apart. The archive bucket already has what this needs and what a new bucket would
have to be given: versioning, SSE at rest, a TLS-only bucket policy, a
public-access block, ap-south-1 residency for DPDP, and an existing IAM grant.
Putting the audio at calls/<callId>/audio/ means one prefix holds everything about
one call, which is also how a person looking for it will search.

STEREO, NOT MIXED
-----------------
The caller is on the left channel and the agent on the right. A mixed mono file
is smaller and answers fewer questions: the recurring failure in this system is
the agent's own speech being transcribed as the caller's answer (see
media/bargein.py and the echo-rejection suite), and on a mixed track that is
unprovable after the fact. Split channels make it audible in one listen.

NEVER FAILS A CALL
------------------
Every entry point swallows its own exceptions and returns None on failure. Audio
is worth a lot after the call and nothing at all during it, so a missing bucket,
a denied PutObject or an S3 outage must not be able to turn a completed intake
into a failed one. Same posture as hvi_archive and the prompt cache.

OFF BY ONE SWITCH
-----------------
HVI_RECORD_CALLS=0 disables it everywhere without touching the pipelines. The
consent wording at A020 is what makes recording lawful here, so a deployment that
changes that wording should also set this to 0.
"""
from __future__ import annotations

import io
import logging
import os
import wave
from typing import Any

log = logging.getLogger("hvi.recording")

DATA_REGION = os.environ.get("DATA_REGION", os.environ.get("AWS_REGION", "ap-south-1"))

# The archive bucket, not AUDIO_BUCKET - see the module docstring. Unset means
# recording is off, exactly as an unset bucket turns the transcript archive off.
BUCKET = os.environ.get("CALL_ARCHIVE_BUCKET", "")
PREFIX = os.environ.get("CALL_ARCHIVE_PREFIX", "calls")

# On unless explicitly disabled. The caller is TOLD the call is recorded before
# any health question is asked, so recording is the behaviour that matches the
# consent we captured; not recording was the mismatch.
ENABLED = os.environ.get("HVI_RECORD_CALLS", "1") not in ("0", "", "false", "no")

_s3: Any = None


def _client() -> Any:
    global _s3
    if _s3 is None:
        import boto3
        _s3 = boto3.client("s3", region_name=DATA_REGION)
    return _s3


def enabled() -> bool:
    """Whether a recording would be written at all. Read at call time, not import
    time, so a test can flip the environment."""
    return bool(BUCKET) and (
        os.environ.get("HVI_RECORD_CALLS", "1") not in ("0", "", "false", "no"))


def recording_key(call_id: str) -> str:
    """The one canonical object key for a call's audio.

    DETERMINISTIC on purpose. hvi_archive probes this exact key to discover a
    recording it did not itself copy, which is what lets the media host - a
    machine with no DynamoDB access - write the audio while the agent still ends
    up reporting the URI to Salesforce.
    """
    return f"{PREFIX}/{call_id}/audio/call.wav"


def recording_uri(call_id: str) -> str:
    return f"s3://{BUCKET}/{recording_key(call_id)}"


def pcm_to_wav(pcm: bytes, sample_rate: int, num_channels: int = 2) -> bytes:
    """Wrap raw signed 16-bit PCM in a WAV container.

    A container rather than raw PCM because the audit story ends with a human
    double-clicking the file. Raw PCM needs the sample rate and channel count
    supplied out of band, and getting either wrong turns a medical record's
    evidence into noise - which is a bad failure for the one artefact that exists
    to settle disputes.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(max(1, int(num_channels)))
        w.setsampwidth(2)  # s16
        w.setframerate(int(sample_rate))
        w.writeframes(pcm)
    return buf.getvalue()


def upload_recording(call_id: str, pcm: bytes, sample_rate: int,
                     num_channels: int = 2) -> str | None:
    """Write one call's audio to the archive. NEVER RAISES; returns the URI or None.

    None covers every reason there is nothing to point at - disabled, no bucket,
    no audio, or a failed put - because the caller's only sensible reaction to all
    four is the same: carry on without a recording URI.
    """
    if not enabled():
        log.info("recording disabled or no archive bucket; not storing audio "
                 "for %s", call_id)
        return None
    if not call_id or not pcm:
        log.info("no audio captured for %s; nothing to store", call_id or "?")
        return None
    try:
        body = pcm_to_wav(pcm, sample_rate, num_channels)
        key = recording_key(call_id)
        _client().put_object(
            Bucket=BUCKET, Key=key, Body=body,
            ContentType="audio/wav",
            ServerSideEncryption="AES256",
            Metadata={"callid": call_id,
                      "samplerate": str(int(sample_rate)),
                      "channels": str(int(num_channels))},
        )
        uri = recording_uri(call_id)
        log.info("stored %.1fs of audio for %s -> %s",
                 len(pcm) / float(max(1, sample_rate * 2 * max(1, num_channels))),
                 call_id, uri)
        return uri
    except Exception as e:  # noqa: BLE001 - a recording never fails a call
        log.warning("could not store the recording for %s: %s", call_id, e)
        return None
