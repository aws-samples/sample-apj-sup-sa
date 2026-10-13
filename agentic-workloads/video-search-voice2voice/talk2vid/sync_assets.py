"""Pull indexes and media down from the private S3 bucket at container start.

Keeping assets in S3 rather than baked into the image means new videos can be
ingested locally and picked up by the deployment on the next task start, with no
rebuild. The bot needs the media files locally because ``look_closer`` extracts
real frames with ffmpeg.
"""

from __future__ import annotations

import sys

import boto3
from loguru import logger

from talk2vid import config

MEDIA_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm")


def sync() -> int:
    if not config.S3_BUCKET:
        logger.warning("TALK2VID_S3_BUCKET is not set; nothing to sync")
        return 0

    s3 = boto3.client("s3", region_name=config.AWS_REGION)
    downloaded = 0

    for prefix, dest_dir, wanted in (
        (f"{config.S3_PREFIX}/index/", config.INDEX_DIR, (".json", ".npz")),
        (f"{config.S3_PREFIX}/media/", config.VIDEO_DIR, MEDIA_SUFFIXES),
    ):
        token = None
        while True:
            kwargs = {"Bucket": config.S3_BUCKET, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = s3.list_objects_v2(**kwargs)
            for obj in page.get("Contents", []):
                key = obj["Key"]
                name = key.rsplit("/", 1)[-1]
                if not name or not name.lower().endswith(wanted):
                    continue
                target = dest_dir / name
                # Size match is enough here: these artifacts are written once.
                if target.exists() and target.stat().st_size == obj["Size"]:
                    continue
                logger.info(f"syncing s3://{config.S3_BUCKET}/{key} -> {target}")
                s3.download_file(config.S3_BUCKET, key, str(target))
                downloaded += 1
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")

    indexes = sorted(p.name for p in config.INDEX_DIR.glob("*.json"))
    media = sorted(p.name for p in config.VIDEO_DIR.glob("*") if p.suffix.lower() in MEDIA_SUFFIXES)
    logger.info(f"assets ready: {len(indexes)} indexes, {len(media)} media files ({downloaded} new)")
    if not indexes:
        logger.error("no indexes available — the library will be empty")
    return 0


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {message}")
    raise SystemExit(sync())
