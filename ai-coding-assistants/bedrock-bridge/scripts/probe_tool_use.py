#!/usr/bin/env python3
"""Raw Bedrock Converse tool-use probe.

Calls converse() directly (no bridge, no Claude Code) against a target model
with one tool definition, then prints the raw response so we can inspect
exactly how the model emits tool_use blocks.

Usage:
    probe_tool_use.py <model_id> [--region REGION]

Examples:
    probe_tool_use.py minimax.minimax-m2.5
    probe_tool_use.py moonshotai.kimi-k2.5 --region ap-northeast-1
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3

TOOL = {
    "toolSpec": {
        "name": "get_weather",
        "description": "Return the current weather for a city.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"},
                    "unit": {"type": "string", "enum": ["c", "f"]},
                },
                "required": ["city"],
            }
        },
    }
}

USER_TURN = {
    "role": "user",
    "content": [{"text": "What's the weather in Tokyo in celsius? Use the get_weather tool."}],
}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("model_id")
    p.add_argument("--region", default="ap-northeast-1")
    args = p.parse_args()

    client = boto3.client("bedrock-runtime", region_name=args.region)

    print(f"→ Probing {args.model_id} in {args.region}")
    print(f"→ Tool: {TOOL['toolSpec']['name']}")
    print()

    try:
        resp = client.converse(
            modelId=args.model_id,
            messages=[USER_TURN],
            toolConfig={"tools": [TOOL]},
            inferenceConfig={"maxTokens": 512, "temperature": 0.2},
        )
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print("=== stopReason ===")
    print(resp.get("stopReason"))
    print()
    print("=== output.message.content (raw blocks) ===")
    for i, block in enumerate(resp.get("output", {}).get("message", {}).get("content", [])):
        print(f"[block {i}] keys={list(block.keys())}")
        print(json.dumps(block, indent=2, default=str))
        print()

    print("=== usage ===")
    print(json.dumps(resp.get("usage", {}), indent=2))

    # Classify result
    blocks = resp.get("output", {}).get("message", {}).get("content", [])
    has_tool_use = any("toolUse" in b for b in blocks)
    if has_tool_use:
        print("\nRESULT: model emitted a structured toolUse block (good).")
    else:
        text = " ".join(b.get("text", "") for b in blocks if "text" in b)
        print("\nRESULT: no toolUse block. Model returned text only:")
        print(repr(text[:500]))


if __name__ == "__main__":
    main()
