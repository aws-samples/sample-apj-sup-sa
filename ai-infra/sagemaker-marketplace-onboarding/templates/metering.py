"""
Per-inference billing metering for SageMaker Marketplace.

This module is opt-in. Only include it if your Marketplace listing uses
per-inference (usage-based) pricing rather than hourly pricing.

Two emission modes, depending on which endpoint is being billed:

1) Standard /invocations (Real-Time, Streaming response, Batch Transform):
   Emit metering as an HTTP RESPONSE header. The value is a JSON string.

       X-Amzn-Inference-Metering: {"Dimension": "inference.count", "ConsumedUnits": 3}

   Mini-batch semantics: if a single /invocations call processes multiple
   inferences (e.g. batching several inputs per request), set ConsumedUnits
   to the number of inferences processed. For a single-inference call, use 1.

   IMPORTANT: AWS Marketplace only charges the buyer for responses with
   HTTP status codes in the 2XX range. Do not emit metering on 4XX/5XX
   responses — it will be ignored. Conversely, if you return 200 with a
   silent error body, you WILL be paid, so make error paths return proper
   non-2XX status codes.

2) Bidirectional WebSocket (/invocations-bidirectional-stream):
   Metering goes on a COMPANION metadata WebSocket at
   /invocations-bidirectional-stream-metadata, NOT on the main data stream.
   Container signals support via X-Amzn-SageMaker-Metadata-Stream-Supported: true
   in the WebSocket upgrade response, then emits frames like:
       {"Metering": {"Dimension": "audio_seconds", "ConsumedUnits": 150,
                     "ClientToken": "<uuid>"}}

The DIMENSION name (e.g., "inference.count", "audio_seconds",
"characters_processed", "tokens") must match what you configure on the
Marketplace listing. SageMaker meters and bills based on this value.

CRITICAL: If per-inference billing is enabled on your listing but the
container fails to emit metering, you will not be paid for those invocations.
Emit metering on EVERY successful /invocations response.

Documentation: https://docs.aws.amazon.com/marketplace/latest/userguide/machine-learning-pricing.html
"""

import json
import uuid
from typing import Any, Dict


# Set this to the dimension name you registered on your Marketplace listing.
# Common values:
#   - "inference.count"        — one unit per invocation (simple)
#   - "audio_seconds"          — seconds of audio processed (STT/TTS)
#   - "characters_synthesized" — characters generated (TTS)
#   - "tokens"                 — LLM tokens produced
#   - "pixels"                 — image pixels processed
#   - "images"                 — image count (image classification/generation)
DIMENSION = "inference.count"


def inference_metering_header(consumed_units: int, dimension: str = DIMENSION) -> str:
    """
    Format the value for the X-Amzn-Inference-Metering response header on a
    standard /invocations response.

    Example:
        response.headers["X-Amzn-Inference-Metering"] = \\
            inference_metering_header(3)
        # → '{"Dimension": "inference.count", "ConsumedUnits": 3}'

    For mini-batch requests where the invocation processes multiple inferences,
    pass the batch size:
        inference_metering_header(len(batch_inputs))

    Only emit this on 2XX responses — Marketplace ignores metering on error
    responses (and does not charge the buyer).
    """
    return json.dumps({
        "Dimension": dimension,
        "ConsumedUnits": int(consumed_units),
    })


def make_metering_frame(
    consumed_units: int,
    dimension: str = DIMENSION,
    client_token: str = "",
) -> Dict[str, Any]:
    """
    Build the JSON metering frame sent on the companion metadata WebSocket
    (/invocations-bidirectional-stream-metadata) for bidirectional streaming
    per-inference billing.

    ClientToken is a per-frame idempotency key. Auto-generated if empty.
    """
    return {
        "Metering": {
            "Dimension": dimension,
            "ConsumedUnits": int(consumed_units),
            "ClientToken": client_token or str(uuid.uuid4()),
        }
    }
