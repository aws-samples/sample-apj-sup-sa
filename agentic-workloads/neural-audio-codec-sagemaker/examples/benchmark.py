"""
Benchmark Mimi and EnCodec on the same endpoint at a matched bitrate band.

Compares the two 24 kHz codecs at their closest available operating points:
Mimi at 1.1 kbps (8 codebooks) and EnCodec at 1.5 kbps (2 codebooks). Reports
frame rate, bitrate, and warm encode/decode latency (median of N invocations).

This measures deterministic, workload-relevant axes only. It does not compute a
cross-codec audio-quality score: objective metrics such as PESQ and VisQOL are not
valid for comparing differently-trained neural codecs. Judge quality by listening
to the samples in ../samples instead.

Usage:
  ENDPOINT_NAME=neural-codec-rt AWS_REGION=us-east-1 python benchmark.py path/to/clip.wav
"""
import os
import sys
import json
import base64
import statistics

import boto3

ENDPOINT = os.environ.get("ENDPOINT_NAME", "neural-codec-rt")
REGION = os.environ.get("AWS_REGION", "us-east-1")
RUNS = int(os.environ.get("RUNS", "5"))
WAV = sys.argv[1] if len(sys.argv) > 1 else "clip.wav"

rt = boto3.client("sagemaker-runtime", region_name=REGION)


def call(payload):
    r = rt.invoke_endpoint(
        EndpointName=ENDPOINT,
        ContentType="application/json",
        Body=json.dumps(payload),
    )
    return json.loads(r["Body"].read())


audio_b64 = base64.b64encode(open(WAV, "rb").read()).decode()

# closest available bitrate band for each codec (matched, not identical)
POINTS = [
    ("Mimi", "mimi", 8, 1.1),
    ("EnCodec", "encodec", 2, 1.5),
]

print(f"{'codec':>8} | {'kbps':>4} | {'frames/s':>8} | {'cb':>2} | {'enc ms':>6} | {'dec ms':>6}")
print("-" * 56)

results = {}
for name, codec, nq, kbps in POINTS:
    enc = call({"codec": codec, "task": "encode", "audio_b64": audio_b64, "num_quantizers": nq})
    enc_ms, dec_ms = [], []
    for _ in range(RUNS):
        e = call({"codec": codec, "task": "encode", "audio_b64": audio_b64, "num_quantizers": nq})
        enc_ms.append(e["encode_ms"])
        d = call({"codec": codec, "task": "decode", "codes": enc["codes"]})
        dec_ms.append(d["decode_ms"])
    em, dm = statistics.median(enc_ms), statistics.median(dec_ms)
    results[name] = dict(kbps=kbps, frames_per_sec=enc["frames_per_sec"],
                         codebooks=nq, enc_ms=round(em, 1), dec_ms=round(dm, 1))
    print(f"{name:>8} | {kbps:>4} | {enc['frames_per_sec']:>8} | {nq:>2} | {em:>6.0f} | {dm:>6.0f}")

json.dump(results, open("benchmark.json", "w"), indent=2)
print("\nBitrate-matched (Mimi 1.1 kbps vs EnCodec 1.5 kbps). Saved benchmark.json")
