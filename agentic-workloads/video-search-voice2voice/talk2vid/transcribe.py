"""Deepgram pre-recorded transcription (ingest-time).

The same vendor powers live STT at runtime, so terminology and formatting stay
consistent between what the agent reads in its context and what it hears.
"""

from __future__ import annotations

from pathlib import Path

import requests
from loguru import logger

from talk2vid import config

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"


def transcribe_file(audio: Path, *, model: str | None = None) -> dict:
    if not config.DEEPGRAM_API_KEY:
        raise RuntimeError("DEEPGRAM_API_KEY is not set")
    params = {
        "model": model or config.STT_MODEL,
        "smart_format": "true",
        "punctuate": "true",
        "paragraphs": "true",
        "utterances": "true",
        "diarize": "true",
        "language": "en",
    }
    logger.info(f"deepgram: transcribing {audio.name} ({audio.stat().st_size / 1e6:.1f} MB)")
    with audio.open("rb") as fh:
        resp = requests.post(
            DEEPGRAM_URL,
            params=params,
            headers={
                "Authorization": f"Token {config.DEEPGRAM_API_KEY}",
                "Content-Type": "audio/wav",
            },
            data=fh,
            timeout=600,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Deepgram {resp.status_code}: {resp.text[:400]}")
    return resp.json()


def parse(result: dict) -> dict:
    """Flatten Deepgram's response into words / utterances / full text."""
    channel = result.get("results", {}).get("channels", [{}])[0]
    alt = (channel.get("alternatives") or [{}])[0]
    words = [
        {
            "w": w.get("punctuated_word") or w.get("word", ""),
            "s": float(w.get("start", 0.0)),
            "e": float(w.get("end", 0.0)),
            "spk": w.get("speaker"),
        }
        for w in alt.get("words", [])
    ]
    utterances = [
        {
            "start": float(u.get("start", 0.0)),
            "end": float(u.get("end", 0.0)),
            "speaker": u.get("speaker"),
            "text": (u.get("transcript") or "").strip(),
        }
        for u in result.get("results", {}).get("utterances", [])
        if (u.get("transcript") or "").strip()
    ]
    return {"text": alt.get("transcript", ""), "words": words, "utterances": utterances}


def words_between(words: list[dict], start: float, end: float) -> str:
    """Transcript text overlapping a time window."""
    return " ".join(w["w"] for w in words if w["e"] > start and w["s"] < end).strip()


def speakers_between(words: list[dict], start: float, end: float) -> list[int]:
    seen: list[int] = []
    for w in words:
        if w["e"] > start and w["s"] < end and w.get("spk") is not None and w["spk"] not in seen:
            seen.append(w["spk"])
    return seen
