"""
Invoke a deployed Whisper Large V3 Turbo SageMaker endpoint.

Examples:
    # Send raw audio bytes
    python invoke.py --endpoint-name whisper-large-v3-turbo --audio sample.wav

    # Translate non-English audio to English, with timestamps
    python invoke.py --endpoint-name whisper-large-v3-turbo --audio sample.mp3 \
        --task translate --timestamps
"""

import argparse
import base64
import json
import mimetypes

import boto3


def parse_args():
    p = argparse.ArgumentParser(description="Invoke the Whisper SageMaker endpoint.")
    p.add_argument("--endpoint-name", required=True)
    p.add_argument("--audio", required=True, help="Path to an audio file (wav/mp3/flac/m4a/...).")
    p.add_argument("--region", default=None)
    p.add_argument("--language", default=None, help="Force a source language, e.g. 'en', 'hi'.")
    p.add_argument("--task", default=None, choices=["transcribe", "translate"])
    p.add_argument("--timestamps", action="store_true", help="Return segment-level timestamps.")
    p.add_argument("--json", action="store_true",
                   help="Send as application/json (base64) instead of raw audio bytes.")
    return p.parse_args()


def main():
    args = parse_args()
    runtime = boto3.client("sagemaker-runtime", region_name=args.region)

    with open(args.audio, "rb") as f:
        audio_bytes = f.read()

    if args.json or args.language or args.task or args.timestamps:
        payload = {"audio": base64.b64encode(audio_bytes).decode("utf-8")}
        if args.language:
            payload["language"] = args.language
        if args.task:
            payload["task"] = args.task
        if args.timestamps:
            payload["return_timestamps"] = True
        body = json.dumps(payload)
        content_type = "application/json"
    else:
        body = audio_bytes
        content_type = mimetypes.guess_type(args.audio)[0] or "audio/wav"

    resp = runtime.invoke_endpoint(
        EndpointName=args.endpoint_name,
        ContentType=content_type,
        Body=body,
    )
    result = json.loads(resp["Body"].read().decode("utf-8"))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
