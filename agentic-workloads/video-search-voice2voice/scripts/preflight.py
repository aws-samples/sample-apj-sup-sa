#!/usr/bin/env python
"""Pre-demo preflight: check every dependency the live demo touches.

Run this a few minutes before presenting. It verifies tooling, API keys, Bedrock
model access, the S3 bucket's security posture and the state of each index, then
measures warm model latency so you know what to expect on stage.

    uv run python scripts/preflight.py
"""

from __future__ import annotations

import shutil
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3  # noqa: E402
import requests  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

from talk2vid import config, index_store  # noqa: E402

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
results: list[tuple[str, str, str]] = []


def record(status: str, label: str, detail: str = "") -> None:
    results.append((status, label, detail))
    icon = {PASS: "\033[32m✓\033[0m", FAIL: "\033[31m✗\033[0m", WARN: "\033[33m!\033[0m"}[status]
    print(f" {icon} {label}" + (f" — {detail}" if detail else ""))


def check_tooling() -> None:
    print("\ntooling")
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        record(PASS if path else FAIL, tool, path or "not on PATH (brew install ffmpeg)")
    with socket.socket() as sock:
        free = sock.connect_ex((config.HOST, config.PORT)) != 0
    record(
        PASS if free else WARN,
        f"port {config.PORT}",
        "free" if free else "in use (an old server may still be running)",
    )


def check_keys() -> None:
    print("\napi keys")
    if not config.DEEPGRAM_API_KEY:
        record(FAIL, "deepgram", "DEEPGRAM_API_KEY not set")
    else:
        try:
            resp = requests.get(
                "https://api.deepgram.com/v1/projects",
                headers={"Authorization": f"Token {config.DEEPGRAM_API_KEY}"},
                timeout=10,
            )
            record(
                PASS if resp.ok else FAIL,
                "deepgram",
                "key valid" if resp.ok else f"HTTP {resp.status_code}",
            )
        except Exception as exc:
            record(FAIL, "deepgram", str(exc)[:80])


def check_world_knowledge() -> None:
    print("\noutside knowledge (agentcore harness)")
    from talk2vid import agentcore

    if not agentcore.available():
        record(
            WARN,
            "agentcore harness",
            "TALK2VID_HARNESS_ARN not set — run scripts/provision_agentcore.py",
        )
        return
    try:
        t0 = time.time()
        result = agentcore.ask_world_sync(
            "In one sentence, what is Amazon Bedrock?", session_id="preflight-warm-session-00000001"
        )
        elapsed = time.time() - t0
        if result.get("answer"):
            # A first call against a freshly created harness can take ~40s while
            # its environment is provisioned; steady state is ~6s.
            record(
                PASS if elapsed < 20 else WARN,
                "web search via harness",
                f"{elapsed:.1f}s · tools={result.get('tools_used')}"
                + ("" if elapsed < 20 else " (cold start — it is warm now)"),
            )
        else:
            record(FAIL, "web search via harness", str(result.get("error"))[:110])
    except Exception as exc:
        record(FAIL, "agentcore harness", str(exc)[:110])


def check_aws() -> None:
    print("\naws")
    try:
        ident = boto3.client("sts", region_name=config.AWS_REGION).get_caller_identity()
        record(PASS, "credentials", f"account {ident['Account']} in {config.AWS_REGION}")
    except Exception as exc:
        record(FAIL, "credentials", str(exc)[:100])
        return

    from talk2vid import bedrock

    checks = [
        ("llm", lambda: bedrock.runtime().converse(
            modelId=config.LLM_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
            inferenceConfig={"maxTokens": 4},
        )),
        ("embeddings", lambda: bedrock.embed_text("preflight")),
    ]
    for label, call in checks:
        try:
            t0 = time.time()
            call()
            record(PASS, f"bedrock {label}", f"{(time.time() - t0) * 1000:.0f}ms warm")
        except Exception as exc:
            record(FAIL, f"bedrock {label}", str(exc)[:110])

    try:
        available = {
            m["modelId"]
            for m in boto3.client("bedrock", region_name=config.AWS_REGION)
            .list_foundation_models()["modelSummaries"]
        }
        for label, model in (
            ("pegasus (ingest)", config.PEGASUS_MODEL_ID),
            ("vision", config.VISION_MODEL_ID.removeprefix("us.")),
            ("embeddings", config.EMBED_MODEL_ID),
        ):
            record(
                PASS if model in available else WARN,
                f"model access {label}",
                model if model in available else f"{model} not listed in this region",
            )
    except Exception as exc:
        record(WARN, "model listing", str(exc)[:90])

    if not config.S3_BUCKET:
        record(WARN, "s3 bucket", "TALK2VID_S3_BUCKET unset — ingest cannot run")
        return
    s3 = boto3.client("s3", region_name=config.AWS_REGION)
    try:
        pab = s3.get_public_access_block(Bucket=config.S3_BUCKET)[
            "PublicAccessBlockConfiguration"
        ]
        blocked = all(pab.values())
        record(
            PASS if blocked else FAIL,
            "s3 bucket private",
            config.S3_BUCKET if blocked else "PUBLIC ACCESS IS NOT FULLY BLOCKED",
        )
    except ClientError as exc:
        record(FAIL, "s3 bucket", str(exc)[:90])


def check_indexes() -> None:
    print("\nvideo indexes")
    indexes = index_store.load_all()
    if not indexes:
        record(FAIL, "indexes", "none found — run talk2vid-ingest on a video first")
        return
    for vid, idx in indexes.items():
        problems = []
        if not idx.source_path.exists():
            problems.append(f"missing media file {idx.source_path}")
        captioned = sum(1 for s in idx.segments if s.get("visual"))
        if captioned < len(idx.segments) * 0.8:
            problems.append(f"only {captioned}/{len(idx.segments)} segments captioned")
        if idx.vectors is None:
            problems.append("no embeddings (semantic search degraded to lexical)")
        if not (idx.meta.get("overview") or {}).get("summary"):
            problems.append("no whole-video overview")
        detail = (
            f"{len(idx.segments)} segments · {captioned} captioned · "
            f"{'embedded' if idx.vectors is not None else 'no vectors'} · "
            f"{len(idx.knowledge_pack())} prompt chars"
        )
        record(
            WARN if problems else PASS,
            f"{vid} ({idx.scenario})",
            detail + (" · " + "; ".join(problems) if problems else ""),
        )
    scenarios = {idx.scenario for idx in indexes.values()}
    record(
        PASS if len(indexes) >= 3 else WARN,
        "demo breadth",
        f"{len(indexes)} video(s) across {len(scenarios)} scenario(s)"
        + ("" if len(indexes) >= 3 else " — the story lands better with 3-4"),
    )


def main() -> int:
    print("Talk2Vid preflight")
    check_tooling()
    check_keys()
    check_aws()
    check_world_knowledge()
    check_indexes()

    failures = [r for r in results if r[0] == FAIL]
    warnings = [r for r in results if r[0] == WARN]
    print(
        f"\n{len(results) - len(failures) - len(warnings)} passed, "
        f"{len(warnings)} warnings, {len(failures)} failures"
    )
    if failures:
        print("\nblocking issues:")
        for _, label, detail in failures:
            print(f"  - {label}: {detail}")
        return 1
    print("\nReady. Start with:  uv run talk2vid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
