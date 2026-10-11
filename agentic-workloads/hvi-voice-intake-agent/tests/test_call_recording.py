#!/usr/bin/env python3
"""Call audio is stored, is a real WAV, and cannot fail a call.

WHAT THIS PINS, AND WHY EACH ONE
--------------------------------
The consent at A020 tells the caller the call is recorded. Until recording
existed that sentence was false and Salesforce's Recording_URI__c was blank on
every call, so the claims worth testing are the ones that make it true and keep
it true:

  1. the bytes we store are a WAV a person can open, at the right rate and
     channel count - raw PCM mislabelled is evidence turned into noise
  2. stereo keeps caller and agent on SEPARATE channels, which is the whole
     reason the recurring "was that the caller or our own echo?" question is
     answerable at all
  3. the key is the SAME callId prefix the transcript is filed under, because a
     recording nothing can find is not a record
  4. hvi_archive DISCOVERS that object and reports it as recordingUri - the media
     host has no DynamoDB access, so discovery is the only thing that gets the URI
     onto the session and from there into the CRM
  5. every failure path returns None instead of raising. Audio is worth a lot
     after the call and nothing during it.

AWS-free: the S3 client is replaced by a fake, so this runs anywhere and asserts
what we SEND rather than what a bucket happened to accept.

Run:  ./.venv/bin/python tests/test_call_recording.py
"""
from __future__ import annotations

import io
import os
import sys
import wave

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

os.environ.setdefault("CALL_ARCHIVE_BUCKET", "hvi-archive-test")
os.environ["HVI_RECORD_CALLS"] = "1"

from common import hvi_archive, hvi_recording  # noqa: E402

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  <{detail}>"))
    if not cond:
        failures.append(label)


class FakeS3:
    """Records what was sent. Optionally refuses, to prove nothing raises."""

    def __init__(self, fail: bool = False, present: set[str] | None = None):
        self.puts: list[dict] = []
        self.fail = fail
        self.present = present or set()

    def put_object(self, **kw):
        if self.fail:
            raise RuntimeError("AccessDenied: no PutObject for you")
        self.puts.append(kw)
        return {}

    def head_object(self, Bucket=None, Key=None, **kw):  # noqa: N803
        if Key not in self.present:
            raise RuntimeError("404 Not Found")
        return {"ContentLength": 1}


def _stereo_pcm(frames: int) -> bytes:
    """Caller-left / agent-right: left channel loud, right channel silent."""
    out = bytearray()
    for _ in range(frames):
        out += (8000).to_bytes(2, "little", signed=True)  # left  = caller
        out += (0).to_bytes(2, "little", signed=True)     # right = agent
    return bytes(out)


CALL = "call-rec-0001"


def main() -> int:
    print("[1] the stored bytes are a real WAV with the right shape")
    pcm = _stereo_pcm(16000)  # 1 second at 16k stereo
    wav = hvi_recording.pcm_to_wav(pcm, 16000, 2)
    check("starts with a RIFF header", wav[:4] == b"RIFF", wav[:4].hex())
    with wave.open(io.BytesIO(wav), "rb") as r:
        check("2 channels", r.getnchannels() == 2, str(r.getnchannels()))
        check("16-bit samples", r.getsampwidth() == 2, str(r.getsampwidth()))
        check("16 kHz", r.getframerate() == 16000, str(r.getframerate()))
        check("one second of frames", r.getnframes() == 16000, str(r.getnframes()))
        read = r.readframes(r.getnframes())
    check("payload survives the container byte for byte", read == pcm)

    print("\n[2] caller and agent stay on separate channels")
    left = [int.from_bytes(read[i:i + 2], "little", signed=True)
            for i in range(0, 400, 4)]
    right = [int.from_bytes(read[i + 2:i + 4], "little", signed=True)
             for i in range(0, 400, 4)]
    check("caller channel carries the caller", set(left) == {8000}, str(set(left)))
    check("agent channel is independent", set(right) == {0}, str(set(right)))

    print("\n[3] the audio is filed under the same callId as the transcript")
    key = hvi_recording.recording_key(CALL)
    check("key sits in the call's own prefix", key == f"calls/{CALL}/audio/call.wav", key)
    check("transcript shares that prefix",
          hvi_archive.transcript_key(CALL, "json") == f"calls/{CALL}/transcript.json",
          hvi_archive.transcript_key(CALL, "json"))

    print("\n[4] upload sends an encrypted object and returns its URI")
    fake = FakeS3()
    hvi_recording._s3 = fake
    uri = hvi_recording.upload_recording(CALL, pcm, 16000, 2)
    # Against the CONFIGURED bucket, not a literal: this suite runs both with and
    # without config/env.sh sourced, and the claim being tested is that the URI
    # names wherever the audio was actually put.
    check("returned the s3 uri", uri == f"s3://{hvi_recording.BUCKET}/{key}", str(uri))
    check("exactly one put", len(fake.puts) == 1, str(len(fake.puts)))
    sent = fake.puts[0] if fake.puts else {}
    check("encrypted at rest", sent.get("ServerSideEncryption") == "AES256",
          str(sent.get("ServerSideEncryption")))
    check("declared as audio/wav", sent.get("ContentType") == "audio/wav",
          str(sent.get("ContentType")))
    check("body is the WAV", sent.get("Body", b"")[:4] == b"RIFF")

    print("\n[5] the archive DISCOVERS the recording and reports it")
    hvi_archive._s3 = FakeS3(present={key})
    check("found by head_object", hvi_archive._find_archived_recording(CALL)
          == f"s3://{hvi_archive.BUCKET}/{key}",
          str(hvi_archive._find_archived_recording(CALL)))
    hvi_archive._s3 = FakeS3(present=set())
    check("absent recording reports None, not a dead link",
          hvi_archive._find_archived_recording(CALL) is None)

    print("\n[6] nothing can fail a call")
    hvi_recording._s3 = FakeS3(fail=True)
    check("a denied put returns None instead of raising",
          hvi_recording.upload_recording(CALL, pcm, 16000, 2) is None)
    hvi_recording._s3 = FakeS3()
    check("no audio returns None", hvi_recording.upload_recording(CALL, b"", 16000, 2) is None)
    check("no call id returns None", hvi_recording.upload_recording("", pcm, 16000, 2) is None)

    print("\n[7] the switch actually switches it off")
    os.environ["HVI_RECORD_CALLS"] = "0"
    check("disabled by env", not hvi_recording.enabled())
    fake = FakeS3()
    hvi_recording._s3 = fake
    check("disabled means nothing is stored",
          hvi_recording.upload_recording(CALL, pcm, 16000, 2) is None
          and not fake.puts)
    os.environ["HVI_RECORD_CALLS"] = "1"
    check("re-enabled without a reimport", hvi_recording.enabled())

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("Call audio is stored, readable, discoverable, and cannot fail a call.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
