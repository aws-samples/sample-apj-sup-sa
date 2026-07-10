"""
Simple load benchmark for the Whisper SageMaker endpoint.

Measures single-request latency and throughput at increasing concurrency by
firing the same audio clip repeatedly. Reports latency percentiles, throughput
(requests/sec), and the real-time factor (audio seconds transcribed per wall
second).

Usage:
    python benchmark.py --endpoint-name whisper-large-v3-turbo --region ap-south-1 \
        --audio sample.wav --audio-duration 4.59 \
        --concurrency 1 2 4 8 --requests-per-level 24
"""

import argparse
import statistics
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.config import Config


def parse_args():
    p = argparse.ArgumentParser(description="Benchmark a Whisper SageMaker endpoint.")
    p.add_argument("--endpoint-name", required=True)
    p.add_argument("--audio", required=True)
    p.add_argument("--region", default=None)
    p.add_argument("--content-type", default="audio/wav")
    p.add_argument("--audio-duration", type=float, default=None,
                   help="Audio length in seconds (auto-detected for .wav).")
    p.add_argument("--concurrency", type=int, nargs="+", default=[1, 2, 4, 8])
    p.add_argument("--requests-per-level", type=int, default=24)
    return p.parse_args()


def audio_seconds(path, override):
    if override:
        return override
    try:
        with wave.open(path) as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return None


def main():
    args = parse_args()
    # Generous client-side pool + timeouts so the client isn't the bottleneck.
    cfg = Config(max_pool_connections=max(args.concurrency) * 2 + 4,
                 retries={"max_attempts": 0}, read_timeout=300, connect_timeout=10)
    runtime = boto3.client("sagemaker-runtime", region_name=args.region, config=cfg)

    with open(args.audio, "rb") as f:
        body = f.read()
    dur = audio_seconds(args.audio, args.audio_duration)

    def one_call():
        t0 = time.perf_counter()
        resp = runtime.invoke_endpoint(
            EndpointName=args.endpoint_name,
            ContentType=args.content_type,
            Body=body,
        )
        resp["Body"].read()
        return time.perf_counter() - t0

    print("Warming up...")
    one_call()

    print(f"\nAudio clip: {args.audio} ({dur:.2f}s)" if dur else f"\nAudio clip: {args.audio}")
    print(f"{'conc':>5} {'reqs':>5} {'rps':>8} {'p50(s)':>8} {'p90(s)':>8} "
          f"{'p99(s)':>8} {'max(s)':>8} {'RTF':>7} {'errors':>7}")

    for conc in args.concurrency:
        n = args.requests_per_level
        latencies, errors = [], 0
        t_start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=conc) as ex:
            futures = [ex.submit(one_call) for _ in range(n)]
            for fut in as_completed(futures):
                try:
                    latencies.append(fut.result())
                except Exception:
                    errors += 1
        wall = time.perf_counter() - t_start

        ok = len(latencies)
        rps = ok / wall if wall > 0 else 0
        latencies.sort()

        def pct(p):
            if not latencies:
                return float("nan")
            idx = min(len(latencies) - 1, int(round(p / 100 * (len(latencies) - 1))))
            return latencies[idx]

        # Real-time factor: audio-seconds processed per wall-second (higher = better).
        rtf = (ok * dur / wall) if (dur and wall > 0) else float("nan")

        print(f"{conc:>5} {n:>5} {rps:>8.2f} {pct(50):>8.3f} {pct(90):>8.3f} "
              f"{pct(99):>8.3f} {(max(latencies) if latencies else float('nan')):>8.3f} "
              f"{rtf:>7.1f} {errors:>7}")

    print("\nNote: numbers are for THIS audio length and ONE instance (unless you "
          "scaled out). Throughput scales ~linearly with instance count; latency "
          "and RTF scale with audio duration.")


if __name__ == "__main__":
    main()
