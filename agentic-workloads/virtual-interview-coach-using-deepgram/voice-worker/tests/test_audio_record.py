"""Tests for consent-gated per-turn audio recording (F006 / G6) — buffer + async S3 upload.

Offline + deterministic: the S3 PUT (_put_object) is monkeypatched, so these prove the buffer, the WAV
wrapping, the consent/bucket gating (no consent / unconfigured bucket -> no upload), and that an upload
failure is swallowed (returns None, never raises into the live loop)."""

from __future__ import annotations

import io
import wave

import pytest

from src import audio_record
from src.audio_record import TurnAudioBuffer
from src.config import Config


def _cfg(monkeypatch, *, bucket: str | None, record: bool = True) -> Config:
    if bucket:
        monkeypatch.setenv("AUDIO_BUCKET", bucket)
    else:
        monkeypatch.delenv("AUDIO_BUCKET", raising=False)
    monkeypatch.setenv("RECORD_AUDIO", "true" if record else "false")
    return Config.load()


def test_buffer_append_take_roundtrips_and_resets():
    buf = TurnAudioBuffer()
    buf.append(b"\x01\x02")
    buf.append(b"\x03\x04")
    assert len(buf) == 4
    assert buf.take() == b"\x01\x02\x03\x04"
    assert buf.take() == b""  # drained/reset
    assert len(buf) == 0


def test_wav_bytes_is_valid_16k_mono_s16():
    pcm = b"\x00\x01" * 1600  # 0.1s of 16kHz mono s16
    wav = audio_record._wav_bytes(pcm)
    with wave.open(io.BytesIO(wav), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16000
        assert w.readframes(w.getnframes()) == pcm


@pytest.mark.asyncio
async def test_upload_returns_uri_and_puts_object(monkeypatch):
    cfg = _cfg(monkeypatch, bucket="audio-bkt")
    captured = {}

    def fake_put(config, key, body):
        captured["key"] = key
        captured["body_len"] = len(body)

    monkeypatch.setattr(audio_record, "_put_object", fake_put)
    uri = await audio_record.upload_turn_audio(cfg, "sess-1", "turn-9", b"\x00\x01" * 800)
    assert uri == "s3://audio-bkt/audio/sess-1/turn-9.wav"
    assert captured["key"] == "audio/sess-1/turn-9.wav"
    assert captured["body_len"] > 0  # a real WAV was uploaded


@pytest.mark.asyncio
async def test_no_consent_means_no_upload(monkeypatch):
    cfg = _cfg(monkeypatch, bucket="audio-bkt", record=False)  # RECORD_AUDIO kill-switch off
    called = {"n": 0}
    monkeypatch.setattr(audio_record, "_put_object", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    uri = await audio_record.upload_turn_audio(cfg, "s", "t", b"\x00\x01" * 800)
    assert uri is None and called["n"] == 0


@pytest.mark.asyncio
async def test_unconfigured_bucket_is_noop(monkeypatch):
    cfg = _cfg(monkeypatch, bucket=None)  # no AUDIO_BUCKET (local/dev)
    called = {"n": 0}
    monkeypatch.setattr(audio_record, "_put_object", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    uri = await audio_record.upload_turn_audio(cfg, "s", "t", b"\x00\x01" * 800)
    assert uri is None and called["n"] == 0


@pytest.mark.asyncio
async def test_empty_pcm_is_noop(monkeypatch):
    cfg = _cfg(monkeypatch, bucket="audio-bkt")
    monkeypatch.setattr(audio_record, "_put_object", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not upload empty")))
    assert await audio_record.upload_turn_audio(cfg, "s", "t", b"") is None


@pytest.mark.asyncio
async def test_upload_failure_is_swallowed(monkeypatch):
    cfg = _cfg(monkeypatch, bucket="audio-bkt")

    def boom(*a, **k):
        raise RuntimeError("s3 down")

    monkeypatch.setattr(audio_record, "_put_object", boom)
    # Must NOT raise into the live loop — returns None, turn just stays without audio.
    assert await audio_record.upload_turn_audio(cfg, "s", "t", b"\x00\x01" * 800) is None
