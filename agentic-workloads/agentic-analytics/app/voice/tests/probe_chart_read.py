"""Probe: invoke the live agent with a chart prompt and run the EXACT bounded-read
logic from analytics_processor, proving the read-cap captures <chart> before the
100MB+ code-interpreter stream bloat triggers IncompleteRead.

Run: cd server && uv run python tests/probe_chart_read.py
"""
import asyncio
import base64
import codecs
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiobotocore.session  # noqa: E402

from auth import get_gateway_token  # noqa: E402
from analytics_processor import _extract_text, split_presenter_output  # noqa: E402
from pipecat.services.aws.utils import resolve_credentials  # noqa: E402

PROMPT = "Show me a pie chart of bookings by unicorn breed"
MAX_BYTES = 8_000_000


async def main():
    region = os.environ["AWS_REGION"]
    arn = os.environ["AWS_AGENT_ARN"]
    token = get_gateway_token()
    params = resolve_credentials(region=region).to_boto_kwargs()
    session = aiobotocore.session.get_session()

    payload = {
        "prompt": PROMPT,
        "gateway_token": token,
        "mode": "voice",
    }

    chunks: list[str] = []
    total_bytes = 0
    done = False
    stop_reason = "stream-end"

    async with session.create_client("bedrock-agentcore", **params) as client:
        resp = await client.invoke_agent_runtime(
            agentRuntimeArn=arn,
            runtimeSessionId="probe-chart-" + "0" * 30,
            payload=json.dumps(payload).encode(),
        )
        is_sse = "text/event-stream" in resp.get("contentType", "")
        decoder = codecs.getincrementaldecoder("utf-8")()
        buf = ""
        try:
            async for chunk in resp.get("response", []):
                total_bytes += len(chunk)
                buf += decoder.decode(chunk)
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    if is_sse:
                        if not line.startswith("data: "):
                            continue
                        text = _extract_text(line[6:])
                    else:
                        text = _extract_text(line)
                    if text:
                        chunks.append(text)
                if total_bytes > MAX_BYTES:
                    done = True
                    stop_reason = "byte-cap"
                    break
            else:
                stop_reason = "stream-end-full-read"
        except Exception as e:
            stop_reason = f"exception:{type(e).__name__}:{e}"

    full = "".join(chunks)
    print(f"\n=== PROBE RESULT ===")
    print(f"stop_reason     : {stop_reason}")
    print(f"bytes_read      : {total_bytes:,}")
    print(f"text_collected  : {len(full):,} chars")
    print(f"has <speak>     : {'<speak>' in full}")
    print(f"has </speak>    : {'</speak>' in full}")
    print(f"has <chart>     : {'<chart' in full}")
    print(f"has </chart>    : {'</chart>' in full}")

    spoken, displayed = split_presenter_output(full)
    print(f"\nspoken          : {spoken[:200]!r}")

    from analytics_processor import _extract_chart_tags
    cleaned, charts = _extract_chart_tags(displayed)
    print(f"displayed (head): {cleaned[:200]!r}")
    print(f"chart tags      : {charts}")

    if not charts:
        print("\nNO <chart s3key=...> tag found in displayed track")
        return

    caption, key = charts[0]
    region = os.environ["AWS_REGION"]
    params = resolve_credentials(region=region).to_boto_kwargs()
    async with session.create_client("s3", **params) as s3:
        url = await s3.generate_presigned_url(
            "get_object", Params={"Bucket": "agentic-analytics-artifacts", "Key": key}, ExpiresIn=3600)
    import urllib.request
    with urllib.request.urlopen(url) as r:
        data = r.read()
    print(f"\ncaption         : {caption!r}")
    print(f"s3key           : {key}")
    print(f"presigned fetch : status={r.status} bytes={len(data):,} valid_png={data[:8] == b'\x89PNG\r\n\x1a\n'}")


if __name__ == "__main__":
    asyncio.run(main())
