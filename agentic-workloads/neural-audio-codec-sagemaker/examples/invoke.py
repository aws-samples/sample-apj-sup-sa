"""
Invoke the real-time endpoint: encode an audio clip to tokens, then decode back.

Usage:
  ENDPOINT_NAME=neural-codec-rt AWS_REGION=us-east-1 python invoke.py path/to/clip.wav

Keep the clip small (the real-time path has a 60-second timeout and a request-size
limit); use the asynchronous endpoint for long audio.
"""
import os
import sys
import json
import base64

import boto3

ENDPOINT = os.environ.get("ENDPOINT_NAME", "neural-codec-rt")
REGION = os.environ.get("AWS_REGION", "us-east-1")
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

print("Encoding...")
enc = call({"codec": "mimi", "task": "encode", "audio_b64": audio_b64, "num_quantizers": 8})
print("  token grid shape:", enc["shape"], "| frames/sec:", enc["frames_per_sec"])

print("Decoding...")
dec = call({"codec": "mimi", "task": "decode", "codes": enc["codes"]})
open("reconstructed.wav", "wb").write(base64.b64decode(dec["audio_b64"]))
print("  wrote reconstructed.wav")
