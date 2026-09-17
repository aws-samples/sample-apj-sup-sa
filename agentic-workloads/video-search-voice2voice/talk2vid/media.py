"""ffmpeg/ffprobe helpers: probing, scene detection, segmentation, keyframes.

Keyframe extraction is on the runtime hot path (the ``look_closer`` tool), so it
is kept to a single seek-then-decode ffmpeg call per frame (~50-80ms for a
local file).
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from talk2vid import config


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} not found on PATH. Install with: brew install ffmpeg")


@dataclass
class Probe:
    duration: float
    width: int
    height: int
    has_audio: bool


def probe(path: Path) -> Probe:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        capture_output=True, text=True, check=True,
    ).stdout
    meta = json.loads(out)
    duration = float(meta["format"].get("duration", 0.0))
    width = height = 0
    has_audio = False
    for stream in meta.get("streams", []):
        if stream.get("codec_type") == "video" and not width:
            width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
            duration = duration or float(stream.get("duration", 0.0))
        if stream.get("codec_type") == "audio":
            has_audio = True
    return Probe(duration=duration, width=width, height=height, has_audio=has_audio)


def extract_audio(path: Path, dest: Path) -> Path:
    """16 kHz mono WAV — what Deepgram's pre-recorded API likes best."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(path),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dest),
        ],
        check=True,
    )
    return dest


def detect_scenes(path: Path, threshold: float | None = None) -> list[tuple[float, float]]:
    """Scene-change candidates as ``(timestamp, score)``.

    A deliberately low floor is used and the score is kept, so segmentation can
    pick the *strongest* cut inside its window instead of guessing one global
    threshold that suits every video (slide decks score ~0.1, match footage ~0.5).
    """
    threshold = config.SCENE_THRESHOLD if threshold is None else threshold
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-v", "info", "-i", str(path),
            "-filter:v", f"select=gt(scene\\,{threshold}),metadata=print:file=-",
            "-an", "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    cuts: list[tuple[float, float]] = []
    pending: float | None = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("frame:"):
            match = re.search(r"pts_time:([0-9.]+)", line)
            pending = float(match.group(1)) if match else None
        elif line.startswith("lavfi.scene_score=") and pending is not None:
            cuts.append((round(pending, 3), float(line.split("=", 1)[1])))
            pending = None
    logger.info(f"scene detection: {len(cuts)} candidate cuts (floor {threshold})")
    return sorted(cuts)


def build_segments(duration: float, cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Cut the timeline into retrieval units, snapping to scene changes.

    Fixed windows split sentences and scenes; snapping to real cuts keeps each
    segment a coherent unit of meaning, which is what makes retrieval accurate.
    """
    target, lo, hi = config.SEGMENT_TARGET_SECS, config.SEGMENT_MIN_SECS, config.SEGMENT_MAX_SECS
    segments: list[tuple[float, float]] = []
    start = 0.0
    while start < duration - 0.5:
        ideal = start + target
        window = [(t, s) for t, s in cuts if start + lo <= t <= start + hi]
        if window:
            # Prefer a strong cut, but stay near the target length.
            end = max(window, key=lambda ts: ts[1] - 0.02 * abs(ts[0] - ideal))[0]
        else:
            end = min(start + target, duration)
        end = min(max(end, start + lo), duration)
        if duration - end < lo:  # absorb a short tail
            end = duration
        segments.append((round(start, 3), round(end, 3)))
        start = end
    return segments or [(0.0, round(duration, 3))]


def keyframe_jpeg(path: Path, timestamp: float, max_width: int = 768, quality: int = 4) -> bytes:
    """Single JPEG at ``timestamp``. Input seeking keeps this fast."""
    proc = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", f"{max(timestamp, 0):.3f}", "-i", str(path),
            "-frames:v", "1", "-vf", f"scale='min({max_width},iw)':-2",
            "-q:v", str(quality), "-f", "image2", "-c:v", "mjpeg", "pipe:1",
        ],
        capture_output=True, check=True,
    )
    return proc.stdout


async def keyframe_jpeg_async(path: Path, timestamp: float, max_width: int = 768) -> bytes:
    return await asyncio.to_thread(keyframe_jpeg, path, timestamp, max_width)


def keyframes_for_segment(path: Path, start: float, end: float, count: int = 2) -> list[bytes]:
    span = max(end - start, 0.1)
    if count == 1:
        stamps = [start + span / 2]
    else:
        stamps = [start + span * (i + 1) / (count + 1) for i in range(count)]
    frames = []
    for stamp in stamps:
        try:
            frames.append(keyframe_jpeg(path, stamp))
        except subprocess.CalledProcessError:
            logger.warning(f"keyframe extraction failed at {stamp:.2f}s")
    return frames


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def hhmmss(seconds: float) -> str:
    seconds = max(int(round(seconds)), 0)
    return f"{seconds // 60:d}:{seconds % 60:02d}"


def spoken_time(seconds: float) -> str:
    """Timestamp phrased for text-to-speech ("2 minutes 15")."""
    seconds = max(int(round(seconds)), 0)
    minutes, secs = divmod(seconds, 60)
    if not minutes:
        return f"{secs} seconds in"
    return f"{minutes} minute{'s' if minutes != 1 else ''} {secs:02d}"
