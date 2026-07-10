"""Dump a histogram of SSE line shapes + sizes from the live agent, to see what
is actually on the wire after the text-delta-only filter."""
import asyncio
import codecs
import json
import os
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiobotocore.session  # noqa: E402
from auth import get_gateway_token  # noqa: E402
from pipecat.services.aws.utils import resolve_credentials  # noqa: E402

PROMPT = "Show me a pie chart of bookings by unicorn breed"


def shape(line: str) -> str:
    try:
        d = json.loads(line)
    except Exception:
        return "NON-JSON"
    if not isinstance(d, dict):
        return f"type:{type(d).__name__}"
    ev = d.get("event")
    if isinstance(ev, dict):
        return "event." + ",".join(sorted(ev.keys()))
    return "top:" + ",".join(sorted(d.keys()))


async def main():
    region = os.environ["AWS_REGION"]
    arn = os.environ["AWS_AGENT_ARN"]
    token = get_gateway_token()
    params = resolve_credentials(region=region).to_boto_kwargs()
    session = aiobotocore.session.get_session()
    payload = {"prompt": PROMPT, "gateway_token": token, "mode": "voice"}

    counts = Counter()
    bytes_by_shape = Counter()
    biggest = ("", 0)
    total = 0
    n_lines = 0
    text_parts = []
    async with session.create_client("bedrock-agentcore", **params) as client:
        resp = await client.invoke_agent_runtime(
            agentRuntimeArn=arn, runtimeSessionId="probe-raw-" + "0" * 30,
            payload=json.dumps(payload).encode())
        decoder = codecs.getincrementaldecoder("utf-8")()
        buf = ""
        try:
            async for chunk in resp.get("response", []):
                total += len(chunk)
                buf += decoder.decode(chunk)
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    if line.startswith("data: "):
                        line = line[6:]
                    if not line.strip():
                        continue
                    n_lines += 1
                    sh = shape(line)
                    counts[sh] += 1
                    bytes_by_shape[sh] += len(line)
                    if len(line) > biggest[1]:
                        biggest = (sh, len(line))
                    try:
                        t = json.loads(line).get("event", {}).get("contentBlockDelta", {}).get("delta", {}).get("text")
                        if t:
                            text_parts.append(t)
                    except Exception:
                        pass
                if total > 40_000_000:
                    print("... stopping dump at 40MB")
                    break
        except Exception as e:
            print(f"stream exception: {type(e).__name__}: {e}")

    print(f"\ntotal bytes read: {total:,}  lines: {n_lines}")
    print(f"biggest line: shape={biggest[0]} size={biggest[1]:,}")
    print("\n=== line-shape histogram (count / total bytes) ===")
    for sh, c in counts.most_common():
        print(f"  {c:6d}  {bytes_by_shape[sh]:>14,}  {sh}")

    full = "".join(text_parts)
    print(f"\n=== assembled text from deltas: {len(full):,} chars ===")
    print(f"has <speak>  : {'<speak>' in full}")
    print(f"has <chart>  : {'<chart' in full}")
    print(f"has </chart> : {'</chart>' in full}")
    print(f"has CHART_B64: {'CHART_B64' in full}")
    import re
    m = re.search(r'<chart[^>]*>(.*?)</chart>', full, re.S)
    if m:
        import base64
        b64 = m.group(1).strip()
        try:
            raw = base64.b64decode(b64)
            print(f"chart b64 len: {len(b64):,}  png bytes: {len(raw):,}  valid PNG: {raw[:8] == b'\x89PNG\r\n\x1a\n'}")
        except Exception as e:
            print(f"chart decode FAILED: {e}")
    # Show what comes right after </speak> so we see the displayed-track head.
    if "</speak>" in full:
        tail = full.split("</speak>", 1)[1]
        print(f"\npost-</speak> head (300): {tail[:300]!r}")


if __name__ == "__main__":
    asyncio.run(main())
