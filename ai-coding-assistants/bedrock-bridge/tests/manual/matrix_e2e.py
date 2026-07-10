#!/usr/bin/env python3
"""End-to-end compatibility matrix: bridge + Claude Code per target model.

For each model, launch `bedrock-bridge --model <id> --print ...` twice:
  1. A text-only turn that forces tool use (Bash echo).
  2. An image turn: Claude Code Reads a PNG file; the tool result contains
     an image block (the shape that previously broke Kimi/MiniMax).

After each run, tail the bridge log file for INFO routing lines and non-socket
errors. Emit a markdown table.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

BRIDGE = os.environ.get("BEDROCK_BRIDGE_BIN") or shutil.which("bedrock-bridge") or "bedrock-bridge"
LOG_DIR = tempfile.gettempdir()
REGION = os.environ.get("AWS_REGION") or "ap-northeast-1"
FIXTURE_IMAGE = Path(__file__).resolve().parent.parent / "fixtures" / "sample_01.jpg"

_PROFILE_PREFIXES = ("global.", "us.", "eu.", "apac.", "jp.", "apne1.", "apne2.", "apne3.")


def model_supports_image(model_id: str) -> bool:
    """Best-effort: does this Bedrock model accept IMAGE input? Resolves
    inference-profile IDs to their underlying foundation model. Defaults to
    True if the lookup fails, so we still run the image turn rather than
    wrongly skip it."""
    try:
        import boto3

        c = boto3.client("bedrock", region_name=REGION)
        mid = model_id
        if model_id.startswith(_PROFILE_PREFIXES):
            prof = c.get_inference_profile(inferenceProfileIdentifier=model_id)
            models = prof.get("models", [])
            if not models:
                return True
            mid = models[0].get("modelArn", "").rsplit("/", 1)[-1]
        r = c.get_foundation_model(modelIdentifier=mid)
        return "IMAGE" in r.get("modelDetails", {}).get("inputModalities", [])
    except Exception:
        return True


MODELS = [
    "moonshotai.kimi-k2.5",
    "moonshot.kimi-k2-thinking",
    "minimax.minimax-m2.5",
    "deepseek.v3.2",
    "qwen.qwen3-235b-a22b-2507-v1:0",
    "qwen.qwen3-coder-480b-a35b-v1:0",
    "qwen.qwen3-vl-235b-a22b",
    "zai.glm-4.7",
    "zai.glm-5",
    "mistral.mistral-large-3-675b-instruct",
    "nvidia.nemotron-super-3-120b",
    "nvidia.nemotron-nano-12b-v2",
    "google.gemma-3-27b-it",
    "apac.amazon.nova-pro-v1:0",
    "qwen.qwen3-next-80b-a3b",
    "openai.gpt-oss-120b-1:0",
    "openai.gpt-oss-20b-1:0",
    "mistral.devstral-2-123b",
    "mistral.magistral-small-2509",
    "qwen.qwen3-32b-v1:0",
    "qwen.qwen3-coder-30b-a3b-v1:0",
    "amazon.nova-lite-v1:0",
    "zai.glm-4.7-flash",
]


def make_probe_image() -> str:
    """Copy the shared test fixture to a neutrally named temp path and return it.

    Uses the same image as the e2e grader. The neutral filename keeps the path
    from hinting at the contents, so a model can't pass by reading the name."""
    import uuid

    dst = os.path.join(LOG_DIR, f"{uuid.uuid4().hex}{FIXTURE_IMAGE.suffix}")
    shutil.copyfile(FIXTURE_IMAGE, dst)
    return dst


def run_claude(model: str, prompt: str, timeout: int = 120) -> tuple[int, str, str, bool]:
    """Spawn bedrock-bridge + Claude Code. Return (exit, stdout, log_path, timed_out).

    --no-session-persistence isolates each turn so transcripts from the prior
    model don't leak into the next (image-turn pollution into a text-turn was
    a recurring false positive without this).
    """
    before = set(glob.glob(f"{LOG_DIR}/bedrock-bridge-*.log"))
    cmd = [
        BRIDGE,
        "--model",
        model,
        "--claude",
        "--print",
        prompt,
        "--",
        "--dangerously-skip-permissions",
        "--no-session-persistence",
    ]
    timed_out = False
    rc = -1
    out = b""
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=LOG_DIR,
            timeout=timeout,
        )
        rc = p.returncode
        out = p.stdout
    except subprocess.TimeoutExpired as e:
        timed_out = True
        out = e.stdout or b""
        # Clean up any lingering bridge/claude child processes from this run
        subprocess.run(
            ["pkill", "-f", f"bedrock-bridge --model {model}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    time.sleep(0.5)
    after = set(glob.glob(f"{LOG_DIR}/bedrock-bridge-*.log"))
    new_logs = sorted(after - before, key=os.path.getmtime)
    log = new_logs[-1] if new_logs else ""
    return rc, out.decode(errors="replace"), log, timed_out


def analyze_log(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {"requests": 0, "routings": [], "errors": []}
    routings: list[tuple[str, str]] = []
    errors: list[str] = []
    with open(path) as f:
        for line in f:
            m = re.search(r"model_in=(\S+).*?routed=(\S+)", line)
            if m:
                routings.append((m.group(1), m.group(2)))
            low = line.lower()
            if ("error" in low or "exception" in low) and "socket.send" not in line:
                errors.append(line.strip())
    return {"requests": len(routings), "routings": routings, "errors": errors}


def classify(stdout: str, log_stats: dict, timed_out: bool = False) -> tuple[str, str]:
    """Return (status, note).

    N/A is used when the bridge or Bedrock reports the model doesn't support
    the requested modality (e.g. image input on a text-only model).
    REFUSED is used when the bridge refuses to start because the configured
    target is an Anthropic Claude model.
    """
    # Bridge preflight refusal happens before the proxy starts, so logs are
    # absent. Detect via the message printed to stderr (captured into stdout
    # because the matrix merges them).
    if "is an Anthropic Claude model" in stdout and "bedrock-bridge does not serve Claude" in stdout:
        return "REFUSED", "bridge refuses Anthropic IDs (use CLAUDE_CODE_USE_BEDROCK natively)"
    if log_stats["errors"]:
        sample = log_stats["errors"][0]
        if "doesn't support the image content block" in sample or "doesn't support the image content" in sample:
            return "N/A", "model has no vision modality"
        m = re.search(r"(ValidationException|AccessDeniedException|ThrottlingException)[^\"]*?: (.{0,120})", sample)
        note = m.group(0)[:120] if m else sample[:120]
        return "FAIL", note
    if timed_out:
        return "TIMEOUT", f"exceeded timeout; {log_stats['requests']} bridge reqs"
    if log_stats["requests"] == 0:
        return "NO_REQ", "claude didn't reach the bridge"
    if "permission" in stdout.lower() and "not have permission" in stdout.lower():
        return "BLOCKED", "claude code refused (permissions)"
    return "OK", ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--out", default="/tmp/bridge_matrix.md")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    probe_png = make_probe_image()
    targets = [m for m in MODELS if not args.only or any(o in m for o in args.only)]

    rows = []
    for m in targets:
        print(f"=== {m} ===", flush=True)
        # Text + Bash tool turn
        tag = m.replace(".", "-")[:20]
        text_prompt = f"Use the Bash tool to run: echo MATRIX_OK_{tag}. Output just the shell output."
        rc1, out1, log1, to1 = run_claude(m, text_prompt, timeout=args.timeout)
        s1 = analyze_log(log1)
        st1, n1 = classify(out1, s1, to1)
        print(f"  text+tool: {st1} | reqs={s1['requests']} | {n1}")
        for pair in set(s1["routings"]):
            print(f"    routed: {pair[0]} -> {pair[1]}")

        # Image-in-tool-result turn. Skip for text-only models: the bridge
        # strips images to a text marker on non-vision targets, so running the
        # turn would report a misleading OK. Mark N/A from the modality probe.
        if not model_supports_image(m):
            st2, n2, log2 = "N/A", "no vision modality", ""
            print(f"  img+tool : {st2} | {n2}")
        else:
            img_prompt = f"Read the image file {probe_png} and describe what you see in one sentence."
            rc2, out2, log2, to2 = run_claude(m, img_prompt, timeout=args.timeout)
            s2 = analyze_log(log2)
            st2, n2 = classify(out2, s2, to2)
            print(f"  img+tool : {st2} | reqs={s2['requests']} | {n2}")
            for pair in set(s2["routings"]):
                print(f"    routed: {pair[0]} -> {pair[1]}")

        rows.append(
            {
                "model": m,
                "text_tool": st1,
                "text_tool_note": n1,
                "img_tool": st2,
                "img_tool_note": n2,
                "log1": log1,
                "log2": log2,
            }
        )

    # Emit markdown
    lines = []
    lines.append(f"# bedrock-bridge compatibility matrix ({time.strftime('%Y-%m-%d')})")
    lines.append("")
    lines.append("Columns:")
    lines.append("- **text+tool**: Claude Code successfully calls `Bash` via the bridge.")
    lines.append("- **image+tool**: Claude Code `Read`s a PNG (image returned inside `tool_result`).")
    lines.append("")
    lines.append("| Model | text+tool | image+tool | notes |")
    lines.append("|-------|-----------|------------|-------|")
    for r in rows:
        note = r["text_tool_note"] or r["img_tool_note"]
        lines.append(f"| `{r['model']}` | {r['text_tool']} | {r['img_tool']} | {note[:90]} |")
    Path(args.out).write_text("\n".join(lines) + "\n")
    print(f"\nmatrix written to {args.out}")


if __name__ == "__main__":
    main()
