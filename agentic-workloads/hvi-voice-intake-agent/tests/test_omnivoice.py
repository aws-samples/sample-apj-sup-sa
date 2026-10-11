#!/usr/bin/env python
"""Smoke test the OmniVoice endpoint: does it speak, in the right languages, fast enough?

Checks what actually matters for a phone call, in order of how badly each would
hurt:
  1. it returns audio at all, at 8kHz signed 16-bit
  2. the audio has ENERGY - a silent-but-well-formed response is the failure mode
     that packet counters and HTTP 200s both miss
  3. Hindi and Hinglish work, not just English. This is the whole reason a
     600-language model was chosen over a stock neural voice, so it is asserted
     rather than assumed
  4. latency leaves room in the turn budget. RTF is the useful number: below 1.0
     means synthesis is faster than playback, which is what barge-in needs

Writes each utterance to build/omnivoice/ so they can be listened to, because
"has energy" is a necessary and not sufficient condition for "sounds right".

Run:  ./.venv/bin/python tests/test_omnivoice.py
"""
from __future__ import annotations

import array
import base64
import json
import math
import os
import statistics
import sys
import time
import wave

import boto3

REGION = os.environ.get("DATA_REGION", "ap-south-1")
ENDPOINT = os.environ.get("OMNIVOICE_ENDPOINT", "hvi-omnivoice")
RATE = 8000
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "build", "omnivoice")

# Same threshold the call-audio analyser uses, for the same reason.
SILENCE_RMS = 150.0

CASES = [
    ("english", "Good afternoon. I am calling about your health insurance policy."),
    ("hindi", "नमस्ते, मैं आपकी स्वास्थ्य बीमा पॉलिसी के बारे में बात करना चाहती हूँ।"),
    ("hinglish", "मुझे आपकी medical history के बारे में कुछ questions पूछने हैं।"),
    ("numeric", "Your policy number is A S 2 0 2 6 0 0 9 8 4 2 3."),
]

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(label)


def rms_of(pcm: bytes) -> tuple[float, int]:
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not samples:
        return 0.0, 0
    peak = max(abs(s) for s in samples)
    return math.sqrt(sum(float(s) * s for s in samples) / len(samples)), peak


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    rt = boto3.client("sagemaker-runtime", region_name=REGION)

    print(f"endpoint: {ENDPOINT} ({REGION})")
    latencies: list[int] = []
    server_ms: list[int] = []

    for name, text in CASES:
        print(f"\n[{name}] {text[:60]}")
        # JSON so the response carries the handler's own generateMs. Wall time
        # measured from this laptop includes the round trip to ap-south-1, which
        # is latency the media box will never pay - it runs in-region. Reporting
        # only wall time would blame the model for the distance to India.
        started = time.perf_counter()
        try:
            resp = rt.invoke_endpoint(
                EndpointName=ENDPOINT,
                ContentType="application/json",
                Accept="application/json",
                Body=json.dumps({"text": text}),
            )
            doc = json.loads(resp["Body"].read())
            pcm = base64.b64decode(doc["audio"])
        except Exception as e:  # noqa: BLE001
            check(f"{name}: synthesis", False, repr(e)[:160])
            continue
        elapsed = int((time.perf_counter() - started) * 1000)
        gen_ms = int(doc.get("generateMs") or 0)
        latencies.append(elapsed)
        server_ms.append(gen_ms)
        print(f"        wall {elapsed}ms, model {gen_ms}ms, "
              f"transport+overhead {elapsed - gen_ms}ms")

        check(f"{name}: returned audio", len(pcm) > 0, f"{len(pcm)} bytes")
        if not pcm:
            continue

        # 16-bit means an even byte count; an odd one indicates a truncated or
        # mis-typed stream rather than audio.
        check(f"{name}: 16-bit aligned", len(pcm) % 2 == 0, f"{len(pcm)} bytes")

        secs = len(pcm) / 2 / RATE
        rms, peak = rms_of(pcm)
        rtf = (elapsed / 1000.0 / secs) if secs else 0.0
        check(f"{name}: has energy", rms > SILENCE_RMS,
              f"rms={rms:.0f} peak={peak}")
        # A phone utterance of a sentence should be seconds, not milliseconds. A
        # very short clip usually means the model stopped early on unknown script.
        check(f"{name}: plausible duration", 0.8 <= secs <= 30.0, f"{secs:.2f}s")
        check(f"{name}: faster than realtime", rtf < 1.0, f"rtf={rtf:.3f} in {elapsed}ms")

        path = os.path.join(OUT_DIR, f"tts-{name}.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm)
        print(f"        wrote {path} ({secs:.2f}s)")

    if latencies:
        wall_p50 = statistics.median(latencies)
        model_p50 = statistics.median(server_ms) if server_ms else 0
        print(f"\nlatency over {len(latencies)} utterances:")
        print(f"  wall  p50 {wall_p50:.0f}ms  max {max(latencies)}ms   "
              f"(includes this machine -> ap-south-1)")
        print(f"  model p50 {model_p50:.0f}ms  max {max(server_ms or [0])}ms   "
              f"(what the in-region media box pays)")

        # THE MODEL TIME IS THE ONE THAT MATTERS. The turn budget is
        # STT + LLM + TTS: hybrid STT measured p50 463ms and the orchestrator's
        # single LLM call is ~600ms, so TTS much above 1s makes turns feel
        # sluggish even though nothing is technically broken.
        check("model p50 leaves room in the turn budget", model_p50 < 1200,
              f"{model_p50:.0f}ms")

        # Advertised RTF is 0.025 on datacentre-class silicon. A T4 is several
        # times slower, so a large gap here is the instance type, not a defect.
        if model_p50 > 1200:
            print("  -> consider ml.g5.xlarge (A10G): roughly 2x the fp16 "
                  "throughput of the T4 in g4dn, and quota has headroom")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OmniVoice OK - listen to build/omnivoice/tts-*.wav to judge quality")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
