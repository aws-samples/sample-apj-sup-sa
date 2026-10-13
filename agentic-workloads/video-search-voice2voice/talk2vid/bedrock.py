"""Amazon Bedrock access layer.

Model roles, and why each was chosen:

* ``amazon.nova-2-multimodal-embeddings-v1:0`` — one embedding space for video,
  audio and text. Async *segmented* mode indexes a whole video (audio + visual
  fused per segment); the sync path embeds the caller's question in ~120ms, so
  semantic "find the moment where…" search stays inside the voice latency budget.
  (TwelveLabs Marengo is the alternative, but it is async-only on Bedrock, which
  costs seconds on the query path.)
* ``twelvelabs.pegasus-1-2-v1:0`` — video-native understanding, used once per
  video at ingest for a whole-video summary and chapter breakdown.
* ``us.amazon.nova-2-lite-v1:0`` — fast image/video reasoning, used at ingest to
  caption each segment and at runtime for the ``look_closer`` tool.
* ``us.anthropic.claude-haiku-4-5-...`` — the conversational brain in the voice
  pipeline (see :mod:`talk2vid.bot`).
"""

from __future__ import annotations

import functools
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
import numpy as np
from botocore.config import Config
from loguru import logger

from talk2vid import config, media

# Short timeouts on the runtime path: a hung call must never stall the voice loop.
_RUNTIME_CFG = Config(
    region_name=config.AWS_REGION,
    retries={"max_attempts": 2, "mode": "adaptive"},
    connect_timeout=3,
    read_timeout=25,
    max_pool_connections=32,
)
_INGEST_CFG = Config(
    region_name=config.AWS_REGION,
    retries={"max_attempts": 4, "mode": "adaptive"},
    connect_timeout=10,
    read_timeout=900,
)


@functools.lru_cache(maxsize=2)
def runtime(ingest: bool = False):
    return boto3.client("bedrock-runtime", config=_INGEST_CFG if ingest else _RUNTIME_CFG)


@functools.lru_cache(maxsize=1)
def s3():
    return boto3.client("s3", region_name=config.AWS_REGION)


@functools.lru_cache(maxsize=1)
def account_id() -> str:
    return boto3.client("sts", region_name=config.AWS_REGION).get_caller_identity()["Account"]


# --------------------------------------------------------------------------- #
# Embeddings
# --------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=256)
def embed_text(text: str, *, purpose: str = "VIDEO_RETRIEVAL") -> np.ndarray:
    """Synchronous text embedding for the query side of semantic video search.

    Cached: rehearsed demo questions and repeated phrasings cost nothing the
    second time. A cold call is ~1.1s and a warm one ~0.4s, hence :func:`warmup`.
    """
    body = {
        "taskType": "SINGLE_EMBEDDING",
        "singleEmbeddingParams": {
            "embeddingPurpose": purpose,
            "embeddingDimension": config.EMBED_DIM,
            "text": {"truncationMode": "END", "value": text},
        },
    }
    resp = runtime().invoke_model(
        modelId=config.EMBED_MODEL_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(resp["body"].read())
    vec = np.asarray(payload["embeddings"][0]["embedding"], dtype=np.float32)
    vec = vec / (np.linalg.norm(vec) + 1e-9)
    vec.setflags(write=False)  # cached value must not be mutated by callers
    return vec


def warmup() -> dict[str, float]:
    """Open TLS connections and prime both runtime models.

    Worth doing before the first question: the same call costs ~1.4s cold and
    ~0.7s warm, and that difference lands straight in voice-to-voice latency.
    """
    timings: dict[str, float] = {}
    t0 = time.time()
    try:
        embed_text("warmup")
        timings["embeddings"] = round(time.time() - t0, 3)
    except Exception as exc:
        logger.warning(f"embedding warmup failed: {exc}")
    t0 = time.time()
    try:
        runtime().converse(
            modelId=config.LLM_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
            inferenceConfig={"maxTokens": 4},
        )
        timings["llm"] = round(time.time() - t0, 3)
    except Exception as exc:
        logger.warning(f"llm warmup failed: {exc}")
    logger.info(f"bedrock warm: {timings}")
    return timings


def embed_video(s3_video_uri: str, *, job_name: str, poll_secs: float = 5.0) -> list[dict]:
    """Async segmented embeddings for a whole video (audio + visual fused).

    Returns ``[{"start": s, "end": s, "embedding": [...]}, ...]``.
    """
    if not config.S3_BUCKET:
        raise RuntimeError("TALK2VID_S3_BUCKET is not set (run scripts/provision_aws.py)")
    out_uri = f"s3://{config.S3_BUCKET}/{config.S3_PREFIX}/embeddings/{job_name}/"
    model_input = {
        "taskType": "SEGMENTED_EMBEDDING",
        "segmentedEmbeddingParams": {
            "embeddingPurpose": "GENERIC_INDEX",
            "embeddingDimension": config.EMBED_DIM,
            "video": {
                "format": "mp4",
                "embeddingMode": "AUDIO_VIDEO_COMBINED",
                "source": {"s3Location": {"uri": s3_video_uri}},
                "segmentationConfig": {"durationSeconds": config.EMBED_SEGMENT_SECS},
            },
        },
    }
    client = runtime(ingest=True)
    started = client.start_async_invoke(
        modelId=config.EMBED_MODEL_ID,
        modelInput=model_input,
        outputDataConfig={"s3OutputDataConfig": {"s3Uri": out_uri}},
    )
    arn = started["invocationArn"]
    logger.info(f"nova-mme: async embedding job started ({arn.rsplit('/', 1)[-1]})")

    t0 = time.time()
    while True:
        job = client.get_async_invoke(invocationArn=arn)
        status = job["status"]
        if status == "Completed":
            logger.info(f"nova-mme: job completed in {time.time() - t0:.0f}s")
            return _read_embeddings(job["outputDataConfig"]["s3OutputDataConfig"]["s3Uri"])
        if status in ("Failed", "Expired"):
            raise RuntimeError(f"embedding job {status}: {job.get('failureMessage')}")
        time.sleep(poll_secs)


def _read_embeddings(out_uri: str) -> list[dict]:
    """Parse the job's S3 output, tolerant to key-naming differences."""
    bucket, _, prefix = out_uri.removeprefix("s3://").partition("/")
    listing = s3().list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
    records: list[dict] = []
    for obj in sorted(listing, key=lambda o: o["Key"]):
        key = obj["Key"]
        if not key.endswith((".json", ".jsonl")):
            continue
        raw = s3().get_object(Bucket=bucket, Key=key)["Body"].read().decode()
        for chunk in _iter_json_docs(raw):
            records.extend(_normalise_embedding_records(chunk))
    if not records:
        raise RuntimeError(f"no embeddings found under {out_uri}")
    records.sort(key=lambda r: r["start"])
    logger.info(f"nova-mme: parsed {len(records)} segment embeddings")
    return records


def _iter_json_docs(raw: str):
    try:
        yield json.loads(raw)
        return
    except json.JSONDecodeError:
        pass
    for line in raw.splitlines():
        line = line.strip()
        if line:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _normalise_embedding_records(doc) -> list[dict]:
    """Accept dict-with-list, bare list, or single record shapes."""
    if isinstance(doc, list):
        items = doc
    elif isinstance(doc, dict):
        items = None
        for key in ("embeddings", "data", "results", "segments"):
            if isinstance(doc.get(key), list):
                items = doc[key]
                break
        if items is None:
            items = [doc]
    else:
        return []

    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        vec = item.get("embedding") or item.get("vector") or item.get("values")
        if not isinstance(vec, list):
            continue
        start = _first_number(item, ("startTime", "startTimeSec", "startSec", "start", "startTimeMillis"))
        end = _first_number(item, ("endTime", "endTimeSec", "endSec", "end", "endTimeMillis"))
        if start is not None and start > 10_000:  # milliseconds
            start, end = start / 1000.0, (end / 1000.0 if end else None)
        out.append(
            {
                "start": float(start or 0.0),
                "end": float(end if end is not None else (start or 0.0) + config.EMBED_SEGMENT_SECS),
                "embedding": vec,
            }
        )
    return out


def _first_number(item: dict, keys: tuple[str, ...]):
    for key in keys:
        val = item.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return None


# --------------------------------------------------------------------------- #
# Visual captioning (ingest) and visual Q&A (runtime)
# --------------------------------------------------------------------------- #
_CAPTION_SYSTEM = (
    "You describe video frames for a retrieval index. Be concrete and dense: who or what "
    "is on screen, actions, setting, on-screen text/graphics/slides quoted verbatim, and any "
    "numbers, logos, scoreboards or UI visible. Never speculate about what happens off screen. "
    "Write 1-2 plain sentences describing only these frames. Do not mention frames, segments, "
    "timestamps or the words 'image' or 'video' — just describe what is shown."
)


def caption_segment(video: Path, start: float, end: float, speech: str = "") -> str:
    """Dense visual description for one segment (2 frames, one model call)."""
    frames = media.keyframes_for_segment(video, start, end, count=2)
    if not frames:
        return ""
    content: list[dict] = [{"image": {"format": "jpeg", "source": {"bytes": f}}} for f in frames]
    prompt = "Describe what is visible in these frames."
    if speech:
        # Grounding the caption in the audio track keeps the two views consistent.
        prompt += f' The words spoken over them are: "{speech[:600]}"'
    content.append({"text": prompt})
    resp = runtime(ingest=True).converse(
        modelId=config.VISION_MODEL_ID,
        system=[{"text": _CAPTION_SYSTEM}],
        messages=[{"role": "user", "content": content}],
        inferenceConfig={"maxTokens": 220, "temperature": 0.2},
    )
    return _converse_text(resp).strip()


def caption_segments(
    video: Path, segments: list[tuple[float, float]], speech: list[str] | None = None
) -> list[str]:
    """One visual description per segment, captioned concurrently.

    Per-segment calls (rather than one batched call describing many segments)
    keep captions correctly aligned to their timestamps.
    """
    speech = speech or [""] * len(segments)
    captions: list[str] = [""] * len(segments)
    done = 0

    def work(i: int) -> tuple[int, str]:
        start, end = segments[i]
        try:
            return i, caption_segment(video, start, end, speech[i] if i < len(speech) else "")
        except Exception as exc:  # captions are enrichment, never fatal
            logger.warning(f"caption failed for segment {i} ({start:.1f}s): {exc}")
            return i, ""

    with ThreadPoolExecutor(max_workers=config.CAPTION_CONCURRENCY) as pool:
        for i, caption in pool.map(work, range(len(segments))):
            captions[i] = caption
            done += 1
            if done % 10 == 0 or done == len(segments):
                logger.info(f"captioned {done}/{len(segments)} segments")
    return captions


def look_closer(frames: list[bytes], question: str, hint: str = "") -> str:
    """Runtime tool: inspect actual pixels to answer a fine-grained question."""
    content: list[dict] = [{"image": {"format": "jpeg", "source": {"bytes": f}}} for f in frames]
    content.append(
        {
            "text": (
                f"{('Context: ' + hint + chr(10)) if hint else ''}"
                f"Question about these frames: {question}\n"
                "Answer in at most 2 short sentences, describing only what is visible. "
                "If it is not visible, say so plainly."
            )
        }
    )
    resp = runtime().converse(
        modelId=config.VISION_MODEL_ID,
        system=[{"text": "You are a precise visual analyst. Never guess."}],
        messages=[{"role": "user", "content": content}],
        inferenceConfig={"maxTokens": 220, "temperature": 0.2},
    )
    return _converse_text(resp).strip()


# --------------------------------------------------------------------------- #
# Whole-video understanding (TwelveLabs Pegasus)
# --------------------------------------------------------------------------- #
_PEGASUS_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Short descriptive title"},
        "summary": {"type": "string", "description": "3-5 sentence summary of the whole video"},
        "topics": {"type": "array", "items": {"type": "string"}},
        "entities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "People, products, teams, organisations or systems that appear",
        },
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_sec": {"type": "number"},
                    "end_sec": {"type": "number"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["start_sec", "end_sec", "title", "description"],
            },
        },
    },
    "required": ["title", "summary", "topics", "entities", "chapters"],
}


def pegasus_overview(s3_video_uri: str, scenario_hint: str = "") -> dict:
    """One video-native pass for summary + chapters (ingest time)."""
    prompt = (
        "Analyse this video end to end. Produce a title, a factual summary, the main topics, "
        "the notable entities, and chapter boundaries with second-accurate start/end times. "
        "Base everything on what is actually said and shown."
    )
    if scenario_hint:
        prompt += f"\nDomain note: {scenario_hint}"
    body = {
        "inputPrompt": prompt,
        "temperature": 0.2,
        "maxOutputTokens": 4096,
        # Pegasus requires the bucket owner account id alongside the S3 URI.
        "mediaSource": {"s3Location": {"uri": s3_video_uri, "bucketOwner": account_id()}},
        "responseFormat": {"jsonSchema": _PEGASUS_SCHEMA},
    }
    resp = runtime(ingest=True).invoke_model(
        modelId=config.PEGASUS_MODEL_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(resp["body"].read())
    text = payload.get("message") or payload.get("outputText") or payload.get("data") or ""
    if isinstance(text, dict):
        return text
    parsed = extract_json(text)
    if not parsed:
        raise RuntimeError(f"could not parse Pegasus output: {str(payload)[:300]}")
    return parsed


def nova_video_overview(s3_video_uri: str, scenario_hint: str = "") -> dict:
    """Fallback whole-video pass using Nova 2 Lite (native video input)."""
    prompt = (
        "Analyse this video end to end and reply with JSON only matching: "
        '{"title":str,"summary":str,"topics":[str],"entities":[str],'
        '"chapters":[{"start_sec":num,"end_sec":num,"title":str,"description":str}]}. '
        "Base everything on what is said and shown."
    )
    if scenario_hint:
        prompt += f" Domain note: {scenario_hint}"
    bucket, _, key = s3_video_uri.removeprefix("s3://").partition("/")
    resp = runtime(ingest=True).converse(
        modelId=config.VISION_MODEL_ID,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "video": {
                            "format": "mp4",
                            "source": {"s3Location": {"uri": f"s3://{bucket}/{key}"}},
                        }
                    },
                    {"text": prompt},
                ],
            }
        ],
        inferenceConfig={"maxTokens": 3000, "temperature": 0.2},
    )
    parsed = extract_json(_converse_text(resp))
    if not parsed:
        raise RuntimeError("could not parse Nova video overview")
    return parsed


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _converse_text(resp: dict) -> str:
    blocks = resp.get("output", {}).get("message", {}).get("content", [])
    return "".join(b.get("text", "") for b in blocks)


def extract_json(text: str):
    """Pull the first JSON object/array out of a model reply."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = candidate.find(opener), candidate.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None
