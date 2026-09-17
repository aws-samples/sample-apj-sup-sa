"""Ingest a video into a talkable index.

Pipeline (all offline, once per video):

  1. probe          ffprobe        duration / resolution / audio presence
  2. transcribe     Deepgram       word-level timestamps + speaker turns
  3. segment        ffmpeg         scene-cut detection -> coherent retrieval units
  4. upload         S3 (private)   handoff location for Bedrock video models
  5. overview       Pegasus 1.2    whole-video summary + chapters (Nova fallback)
  6. caption        Nova 2 Lite    dense visual description per segment
  7. embed          Nova 2 MME     async segmented audio+visual embeddings
  8. save           local + S3     JSON index + .npz vectors

Every enrichment step degrades gracefully: if Pegasus, captions or embeddings
fail, you still get a talkable video from transcript + timeline.

    uv run talk2vid-ingest videos/reinvent.mp4 --scenario cloud --title "AWS re:Invent keynote"
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
from loguru import logger

from talk2vid import bedrock, config, index_store, media, transcribe


def slugify(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-") or "video"


def upload_video(path: Path, video_id: str) -> str:
    if not config.S3_BUCKET:
        raise RuntimeError("TALK2VID_S3_BUCKET is not set — run: uv run python scripts/provision_aws.py")
    key = f"{config.S3_PREFIX}/media/{video_id}{path.suffix.lower()}"
    s3 = boto3.client("s3", region_name=config.AWS_REGION)
    try:
        head = s3.head_object(Bucket=config.S3_BUCKET, Key=key)
        if head["ContentLength"] == path.stat().st_size:
            logger.info("s3: already uploaded, reusing")
            return f"s3://{config.S3_BUCKET}/{key}"
    except Exception:
        pass
    logger.info(f"s3: uploading {path.name} ({path.stat().st_size / 1e6:.1f} MB)")
    s3.upload_file(
        str(path), config.S3_BUCKET, key, ExtraArgs={"ServerSideEncryption": "AES256"}
    )
    return f"s3://{config.S3_BUCKET}/{key}"


def ingest(
    path: Path,
    *,
    video_id: str | None = None,
    title: str | None = None,
    scenario: str = "general",
    skip_overview: bool = False,
    skip_captions: bool = False,
    skip_embeddings: bool = False,
) -> str:
    media.require_ffmpeg()
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(path)

    video_id = video_id or slugify(path.stem)
    title = title or path.stem.replace("_", " ").replace("-", " ").title()
    persona = config.scenario(scenario).persona
    t_start = time.time()
    logger.info(f"=== ingest {video_id} ({scenario}) ===")

    # A partial re-ingest (--skip-*) must not throw away work from a previous
    # run, so load whatever already exists and reuse the skipped parts.
    previous = None
    if index_store.index_path(video_id).exists():
        try:
            previous = index_store.load(video_id)
            logger.info("found an existing index; skipped steps will reuse it")
        except Exception as exc:
            logger.warning(f"could not read existing index: {exc}")

    # 1. probe
    info = media.probe(path)
    logger.info(
        f"probe: {info.duration:.1f}s {info.width}x{info.height} audio={info.has_audio}"
    )

    # 2. transcribe
    parsed = {"text": "", "words": [], "utterances": []}
    if info.has_audio:
        wav = config.CACHE_DIR / f"{video_id}.wav"
        media.extract_audio(path, wav)
        try:
            parsed = transcribe.parse(transcribe.transcribe_file(wav))
            logger.info(
                f"transcript: {len(parsed['words'])} words, {len(parsed['utterances'])} utterances"
            )
        except Exception as exc:
            logger.error(f"transcription failed: {exc}")
        finally:
            wav.unlink(missing_ok=True)
    else:
        logger.warning("no audio track — visual-only index")

    # 3. segment on scene cuts
    cuts = media.detect_scenes(path)
    spans = media.build_segments(info.duration, cuts)
    logger.info(f"segments: {len(spans)} (avg {info.duration / max(len(spans), 1):.1f}s)")

    segments = [
        {
            "i": i,
            "start": start,
            "end": end,
            "speech": transcribe.words_between(parsed["words"], start, end),
            "visual": "",
            "speakers": transcribe.speakers_between(parsed["words"], start, end),
        }
        for i, (start, end) in enumerate(spans)
    ]

    # 4. upload (needed by Pegasus + the async embedding job)
    s3_uri = None
    if not (skip_overview and skip_embeddings):
        try:
            s3_uri = upload_video(path, video_id)
        except Exception as exc:
            logger.error(f"upload failed, skipping S3-backed steps: {exc}")

    # 5. whole-video overview
    overview: dict = {}
    if skip_overview and previous:
        overview = previous.meta.get("overview") or {}
        if overview:
            logger.info("overview: reused from existing index")
    if s3_uri and not skip_overview:
        try:
            logger.info("pegasus: analysing whole video…")
            overview = bedrock.pegasus_overview(s3_uri, persona)
            overview["source"] = config.PEGASUS_MODEL_ID
        except Exception as exc:
            logger.warning(f"pegasus failed ({exc}); falling back to Nova video overview")
            try:
                overview = bedrock.nova_video_overview(s3_uri, persona)
                overview["source"] = config.VISION_MODEL_ID
            except Exception as exc2:
                logger.error(f"video overview unavailable: {exc2}")
    if overview.get("title") and not title:
        title = overview["title"]

    # 6. per-segment visual captions
    if not skip_captions:
        captions = bedrock.caption_segments(path, spans, [s["speech"] for s in segments])
        for seg, caption in zip(segments, captions):
            seg["visual"] = caption
    elif previous:
        reused = 0
        for seg in segments:
            mid = (seg["start"] + seg["end"]) / 2
            idx = previous.segment_at(mid)
            if idx is not None and previous.segments[idx].get("visual"):
                seg["visual"] = previous.segments[idx]["visual"]
                reused += 1
        if reused:
            logger.info(f"captions: reused {reused}/{len(segments)} from existing index")

    # 7. segmented multimodal embeddings
    embeddings: list[dict] = []
    if skip_embeddings and previous is not None and previous.vectors is not None:
        embeddings = [
            {"start": float(s), "end": float(e), "embedding": vec.tolist()}
            for (s, e), vec in zip(previous.vector_spans, previous.vectors)
        ]
        logger.info(f"embeddings: reused {len(embeddings)} from existing index")
    if s3_uri and not skip_embeddings:
        try:
            embeddings = bedrock.embed_video(s3_uri, job_name=f"{video_id}-{int(time.time())}")
        except Exception as exc:
            logger.error(f"embedding job failed ({exc}); lexical search only")

    # 8. persist
    meta = {
        "title": title,
        "scenario": scenario,
        "source_file": str(path),
        "s3_uri": s3_uri,
        "duration": info.duration,
        "width": info.width,
        "height": info.height,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overview": overview,
        "transcript_text": parsed["text"],
        "utterances": parsed["utterances"],
        "models": {
            "transcription": f"deepgram:{config.STT_MODEL}",
            "overview": overview.get("source"),
            "captions": None if skip_captions else config.VISION_MODEL_ID,
            "embeddings": config.EMBED_MODEL_ID if embeddings else None,
        },
    }
    index_store.save(video_id, meta, segments, embeddings)
    try:
        index_store.push_to_s3(video_id)
    except Exception as exc:
        logger.warning(f"index S3 sync skipped: {exc}")

    logger.info(
        f"=== done {video_id} in {time.time() - t_start:.0f}s | "
        f"{len(segments)} segments, {len(embeddings)} embedded ==="
    )
    return video_id


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest a video for Talk to your Video")
    ap.add_argument("video", type=Path, help="path to an .mp4")
    ap.add_argument("--id", dest="video_id", help="index id (default: slug of filename)")
    ap.add_argument("--title", help="display title")
    ap.add_argument(
        "--scenario", default="general", choices=sorted(config.SCENARIOS), help="demo persona"
    )
    ap.add_argument("--skip-overview", action="store_true")
    ap.add_argument("--skip-captions", action="store_true")
    ap.add_argument("--skip-embeddings", action="store_true")
    args = ap.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | {message}")
    ingest(
        args.video,
        video_id=args.video_id,
        title=args.title,
        scenario=args.scenario,
        skip_overview=args.skip_overview,
        skip_captions=args.skip_captions,
        skip_embeddings=args.skip_embeddings,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
