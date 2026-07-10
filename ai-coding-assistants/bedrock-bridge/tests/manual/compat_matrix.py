#!/usr/bin/env python3
"""Bedrock model compatibility matrix for bedrock-bridge.

For each model, probe four capabilities we care about in Claude Code:

  1. text         : plain text response
  2. tool_use     : emits a structured toolUse block
  3. image_tr     : accepts an image inside toolResult.content
  4. stream       : ConverseStream works

Writes a markdown table to stdout so the README can embed it.
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
import zlib
from typing import Any, Callable

import boto3

REGION_DEFAULT = "ap-northeast-1"

MODELS_TO_TEST = [
    "anthropic.claude-opus-4-7",
    "anthropic.claude-sonnet-4-6",
    "anthropic.claude-haiku-4-5-20251001-v1:0",
    "moonshotai.kimi-k2.5",
    "moonshot.kimi-k2-thinking",
    "minimax.minimax-m2.5",
    "deepseek.v3.2",
    "qwen.qwen3-235b-a22b-2507-v1:0",
    "qwen.qwen3-coder-480b-a35b-v1:0",
    "qwen.qwen3-vl-235b-a22b",
    "zai.glm-4.7",
    "zai.glm-5",
    "meta.llama4-maverick-17b-instruct-v1:0",
    "meta.llama4-scout-17b-instruct-v1:0",
    "mistral.mistral-large-3-675b-instruct",
]


def make_png(w: int = 48, h: int = 48, rgb: tuple[int, int, int] = (0, 128, 255)) -> bytes:
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    sig = b"\x89PNG\r\n\x1a\n"
    return (
        sig
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\x00" + bytes(rgb) * w) * h))
        + chunk(b"IEND", b"")
    )


WEATHER_TOOL = {
    "toolSpec": {
        "name": "get_weather",
        "description": "Return the current weather for a city.",
        "inputSchema": {"json": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}},
    }
}
SCREENSHOT_TOOL = {
    "toolSpec": {
        "name": "screenshot",
        "description": "Take a screenshot.",
        "inputSchema": {"json": {"type": "object"}},
    }
}


def _err_snippet(e: Exception, n: int = 140) -> str:
    s = str(e).replace("An error occurred (ValidationException) when calling the ", "")
    return s[:n].replace("\n", " ").strip()


def probe_text(client: Any, model_id: str) -> bool:
    r = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": "Reply with exactly: PONG"}]}],
        inferenceConfig={"maxTokens": 20},
    )
    txt = " ".join(b.get("text", "") for b in r["output"]["message"]["content"] if "text" in b)
    return "PONG" in txt.upper() or bool(txt.strip())


def probe_tool_use(client: Any, model_id: str) -> bool:
    r = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": "What's the weather in Tokyo? Use the tool."}]}],
        toolConfig={"tools": [WEATHER_TOOL]},
        inferenceConfig={"maxTokens": 200, "temperature": 0.0},
    )
    return any("toolUse" in b for b in r["output"]["message"]["content"])


def probe_image_in_tool_result(client: Any, model_id: str) -> bool:
    png = make_png()
    client.converse(
        modelId=model_id,
        messages=[
            {"role": "user", "content": [{"text": "screenshot please"}]},
            {"role": "assistant", "content": [{"toolUse": {"toolUseId": "t1", "name": "screenshot", "input": {}}}]},
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "t1",
                            "content": [
                                {"text": "here it is:"},
                                {"image": {"format": "png", "source": {"bytes": png}}},
                            ],
                        }
                    }
                ],
            },
        ],
        toolConfig={"tools": [SCREENSHOT_TOOL]},
        inferenceConfig={"maxTokens": 60},
    )
    return True  # if no exception raised, accepted


def probe_stream(client: Any, model_id: str) -> bool:
    r = client.converse_stream(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": "say hi"}]}],
        inferenceConfig={"maxTokens": 10},
    )
    for _ in r["stream"]:
        pass
    return True


TESTS: list[tuple[str, Callable]] = [
    ("text", probe_text),
    ("tool_use", probe_tool_use),
    ("image_tr", probe_image_in_tool_result),
    ("stream", probe_stream),
]


def run(model_id: str, client: Any) -> dict[str, str]:
    results: dict[str, str] = {}
    for name, fn in TESTS:
        try:
            ok = fn(client, model_id)
            results[name] = "OK" if ok else "NO"
            results[name + "_note"] = ""
        except Exception as e:
            results[name] = "FAIL"
            results[name + "_note"] = _err_snippet(e)
        time.sleep(0.2)
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default=REGION_DEFAULT)
    ap.add_argument("--only", nargs="*", help="Restrict to these model IDs")
    args = ap.parse_args()

    client = boto3.client("bedrock-runtime", region_name=args.region)
    models = [m for m in MODELS_TO_TEST if not args.only or any(o in m for o in args.only)]

    width = max(len(m) for m in models) + 2
    hdr = f"| {'modelId':<{width}} | text | tool_use | image_tr | stream | notes |"
    sep = f"|{'-' * (width + 2)}|------|----------|----------|--------|-------|"
    print(hdr)
    print(sep)

    notes_out: list[str] = []
    for m in models:
        sys.stderr.write(f"{m} ... ")
        sys.stderr.flush()
        r = run(m, client)
        notes = []
        for t, _ in TESTS:
            n = r.get(t + "_note", "")
            if n:
                notes.append(f"{t}: {n}")
        note_col = notes[0] if notes else ""
        print(
            f"| `{m:<{width - 2}}` | {r['text']:<4} | {r['tool_use']:<8} | "
            f"{r['image_tr']:<8} | {r['stream']:<6} | {note_col[:80]} |"
        )
        sys.stderr.write("done\n")
        if len(notes) > 1:
            notes_out.append((m, notes))

    if notes_out:
        print("\n### Extra notes")
        for m, ns in notes_out:
            print(f"\n- `{m}`")
            for n in ns:
                print(f"  - {n}")


if __name__ == "__main__":
    main()
