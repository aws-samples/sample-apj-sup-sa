#!/usr/bin/env python
"""Generate a narrated synthetic video so the full stack can be smoke-tested
without waiting on real demo assets.

Slides are rendered with Pillow, narration with Deepgram Aura-2, then muxed with
ffmpeg. The result has real speech, real scene cuts and real on-screen text —
enough to exercise transcription, captioning, embeddings and retrieval.

    uv run python scripts/make_test_video.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from talk2vid import config  # noqa: E402

SLIDES = [
    (
        "Talk to your Video",
        ["Demo smoke test", "Powered by Amazon Bedrock"],
        "#0f1220",
        "Welcome to the Talk to your Video smoke test. This clip exists to prove the "
        "ingest pipeline works end to end.",
    ),
    (
        "Architecture",
        ["Pipecat voice pipeline", "Deepgram Nova-3 speech to text", "Claude Haiku on Bedrock"],
        "#12233a",
        "The voice pipeline runs on Pipecat, with Deepgram Nova three for speech to text and "
        "Claude Haiku four point five on Amazon Bedrock as the reasoning model.",
    ),
    (
        "Video understanding",
        ["TwelveLabs Pegasus 1.2", "Nova 2 multimodal embeddings", "Segment level captions"],
        "#1c2b1f",
        "Video understanding comes from TwelveLabs Pegasus and Nova multimodal embeddings, "
        "which index every fifteen second segment of audio and vision together.",
    ),
    (
        "The magic number is 42",
        ["Remember this slide", "Order reference: ZULU-7781"],
        "#3a1220",
        "Here is a detail worth remembering. The magic number is forty two, and the order "
        "reference is Zulu seven seven eight one.",
    ),
    (
        "Ask me anything",
        ["Latency target: under one second", "Say: show me the magic number"],
        "#241a3a",
        "Now you can simply ask a question out loud. Try saying, show me the magic number, and "
        "the player will jump straight to that moment.",
    ),
]


def font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size)


def render_slide(title: str, bullets: list[str], bg: str, dest: Path) -> None:
    img = Image.new("RGB", (1280, 720), bg)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 1280, 8], fill="#ff9900")
    draw.text((80, 140), title, font=font(74), fill="#ffffff")
    for i, line in enumerate(bullets):
        draw.ellipse([84, 306 + i * 78, 100, 322 + i * 78], fill="#ff9900")
        draw.text((124, 292 + i * 78), line, font=font(40), fill="#d7dce8")
    draw.text((80, 640), "talk2vid · synthetic test asset", font=font(26), fill="#7f8899")
    img.save(dest, quality=92)


def narrate(text: str, dest: Path) -> None:
    resp = requests.post(
        "https://api.deepgram.com/v1/speak",
        params={"model": config.TTS_VOICE, "encoding": "linear16", "sample_rate": "24000"},
        headers={
            "Authorization": f"Token {config.DEEPGRAM_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"text": text},
        timeout=120,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Deepgram TTS {resp.status_code}: {resp.text[:200]}")
    dest.write_bytes(resp.content)


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def main() -> int:
    if not config.DEEPGRAM_API_KEY:
        print("DEEPGRAM_API_KEY missing", file=sys.stderr)
        return 1
    out_path = config.VIDEO_DIR / "smoke-test.mp4"
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        clips = []
        for i, (title, bullets, bg, script) in enumerate(SLIDES):
            png, wav, clip = tmp / f"s{i}.jpg", tmp / f"s{i}.wav", tmp / f"c{i}.mp4"
            render_slide(title, bullets, bg, png)
            narrate(script, wav)
            secs = duration(wav) + 0.6
            subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error",
                    "-loop", "1", "-i", str(png),
                    "-f", "lavfi", "-t", f"{secs:.2f}", "-i", "anullsrc=r=24000:cl=mono",
                    "-i", str(wav),
                    "-filter_complex", "[1:a][2:a]amix=inputs=2:duration=first[a]",
                    "-map", "0:v", "-map", "[a]",
                    "-t", f"{secs:.2f}", "-r", "12",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
                    "-c:a", "aac", "-ar", "24000",
                    str(clip),
                ],
                check=True,
            )
            clips.append(clip)
            print(f"  slide {i + 1}/{len(SLIDES)} · {secs:.1f}s · {title}")

        listing = tmp / "list.txt"
        listing.write_text("".join(f"file '{c}'\n" for c in clips))
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
                "-c:a", "aac", "-movflags", "+faststart", str(out_path),
            ],
            check=True,
        )
    print(f"wrote {out_path} ({duration(out_path):.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
