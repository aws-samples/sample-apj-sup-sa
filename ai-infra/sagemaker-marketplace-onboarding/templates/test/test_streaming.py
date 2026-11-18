"""
Test streaming response from /invocations.

The container returns Transfer-Encoding: chunked; each chunk becomes a
PayloadPart event on the SageMaker client side when the customer uses
InvokeEndpointWithResponseStream.

This test simulates that by reading the raw HTTP response in chunks and
confirming they arrive progressively rather than all at once.
"""

import json
import sys
import time

import requests


def main(url: str = "http://127.0.0.1:8080/invocations",
         payload_path: str = "test/test_input.json",
         accept: str = "application/x-ndjson") -> None:
    with open(payload_path, "rb") as f:
        payload = f.read()

    headers = {"Content-Type": "application/json", "Accept": accept}
    print(f"POST {url}")
    print(f"  Accept: {accept}")

    start = time.perf_counter()
    with requests.post(url, data=payload, headers=headers, stream=True, timeout=480) as r:
        r.raise_for_status()
        print(f"  Status: {r.status_code}")
        print(f"  Transfer-Encoding: {r.headers.get('Transfer-Encoding', '(none)')}")
        if r.headers.get("Transfer-Encoding") != "chunked":
            print("  WARNING: response is not chunked. Streaming may not be enabled.")

        chunk_count = 0
        first_chunk_at = None
        for chunk in r.iter_content(chunk_size=None):
            if not chunk:
                continue
            if first_chunk_at is None:
                first_chunk_at = time.perf_counter() - start
            chunk_count += 1
            elapsed = time.perf_counter() - start
            print(f"  [chunk #{chunk_count} at +{elapsed:.2f}s, {len(chunk)}B]: "
                  f"{chunk[:120]!r}")

    if chunk_count < 2:
        print("  WARNING: only one chunk received. Streaming does not appear to work.")
        print("  Confirm predict_stream() yields incrementally and app.py uses "
              "StreamingResponse for this Accept type.")
        sys.exit(1)

    print(f"  Time-to-first-chunk: {first_chunk_at:.2f}s (target: <1s for good UX)")


if __name__ == "__main__":
    main()
