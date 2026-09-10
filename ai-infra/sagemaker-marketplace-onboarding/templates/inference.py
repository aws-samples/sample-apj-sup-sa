"""
Framework-specific inference logic. The SKILL walkthrough will help fill this
in based on the framework selected during Discovery.

Three entry points:
- smoke_check(model)                          — cheap sanity check run on every
                                                /ping request. REQUIRED. Do not
                                                make /ping a static 200.
- predict(model, body, content_type, accept)  — single request/response
- predict_stream(model, body, content_type)   — async generator that yields
                                                chunks (only needed if streaming
                                                was selected in Discovery)
"""

import io
import json
import zipfile
from typing import AsyncIterator, Dict, Tuple


def zip_outputs(files: Dict[str, bytes]) -> bytes:
    """Zip multiple output files for a single /invocations response.

    Use when your model produces >1 output artifact per invocation (e.g.
    generated image + metadata json, transcribed text + speaker labels).
    The container cannot write to S3 at runtime — return everything in one
    zip and let the customer split on their side.

    Set Content-Type: application/zip on the response (see predict() return).

    Example:
        return zip_outputs({
            "image.png": png_bytes,
            "metadata.json": json.dumps(meta).encode(),
        }), "application/zip"

    Keeps the zip in memory (io.BytesIO) — do not use /tmp unless the outputs
    are very large (say, >100 MB total).
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for filename, data in files.items():
            zf.writestr(filename, data)
    return buf.getvalue()


def smoke_check(model) -> None:
    """Verify the inference code path is actually alive.

    Called on every /ping request. Must complete well under 2 seconds. Raise
    an exception (or return without raising) — the caller in app.py converts
    exceptions to 503. Do NOT swallow errors here.

    The spec is explicit: `/ping` that always returns 200 while the model is
    broken keeps SageMaker routing traffic to the dead instance. This function
    is what makes /ping *actually* meaningful.

    Keep it CHEAP. Some cheap smoke checks by modality:
      LLM      → generate 1 token from a fixed prompt
      STT      → decode 100ms of pre-baked silence
      TTS      → synthesize a single phoneme
      Image    → forward-pass on a tiny (e.g. 8x8 or 32x32) tensor
      Embed    → embed a fixed 1-token string
      ONNX     → session.run on a cached warm-up tensor

    Common pattern: run the smoke inference ONCE at startup, cache the input
    tensor, and reuse it here.
    """
    # ------------------------------------------------------------------
    # TODO — replace with a cheap inference call.
    #
    # PyTorch example:
    #   with torch.no_grad():
    #       _ = model(cached_smoke_input)   # cached_smoke_input built at load time
    #
    # HuggingFace generation example:
    #   _ = model["model"].generate(cached_input_ids, max_new_tokens=1)
    #
    # ONNX Runtime example:
    #   _ = model.run(None, {"input": cached_np_input})
    # ------------------------------------------------------------------
    raise NotImplementedError(
        "Implement smoke_check() — /ping must verify the inference path, "
        "not just object presence. See the spec §3.2."
    )


def predict(model, body: bytes, content_type: str, accept: str) -> Tuple[bytes, str]:
    """Run one inference and return (response_bytes, response_content_type).

    Parameters
    ----------
    model : the object returned by model_loader.load_model()
    body  : raw request bytes. Up to 25 MB for Real-Time, 100 MB per record
            for Batch Transform.
    content_type : the buyer's Content-Type header (e.g. "application/json",
                   "audio/wav"). Use this to parse the body.
    accept : the buyer's Accept header. Use to decide response encoding.

    Returns
    -------
    (response_bytes, response_content_type)
        response_content_type should be "application/json" for JSON,
        "application/zip" if you zipped multiple output files, etc.
    """
    # ------------------------------------------------------------------
    # TODO — replace with your model's predict logic.
    #
    # Common patterns:
    #
    # JSON in, JSON out:
    #     payload = json.loads(body)
    #     result = model.predict(payload["input"])
    #     return json.dumps({"output": result}).encode(), "application/json"
    #
    # Audio in, JSON out (transcription):
    #     with io.BytesIO(body) as buf:
    #         waveform, sr = torchaudio.load(buf)
    #     text = model.transcribe(waveform, sr)
    #     return json.dumps({"text": text}).encode(), "application/json"
    #
    # Multiple output files -> zip (see zip_outputs() helper below):
    #     outputs = {"result.png": png_bytes, "metadata.json": meta_bytes}
    #     return zip_outputs(outputs), "application/zip"
    #
    # If a step needs disk (e.g. ffmpeg piping through a temp file):
    #   /tmp IS THE ONLY WRITABLE PATH. /opt/ml/model is read-only.
    #   Use tempfile.NamedTemporaryFile(dir="/tmp"), and always clean up —
    #   /tmp is shared across concurrent requests and does not survive
    #   container restarts.
    #     with tempfile.NamedTemporaryFile(dir="/tmp", suffix=".wav") as tf:
    #         tf.write(body); tf.flush()
    #         result = subprocess.run(["ffmpeg", "-i", tf.name, ...], ...)
    # ------------------------------------------------------------------
    raise NotImplementedError("Replace predict() with your model's inference logic.")


async def predict_stream(model, body: bytes, content_type: str) -> AsyncIterator[bytes]:
    """Yield response chunks for InvokeEndpointWithResponseStream.

    Only needed if streaming response was selected in Phase 0. Each yielded
    bytes object becomes a PayloadPart event on the client side.

    Example (LLM token streaming):
        payload = json.loads(body)
        for token in model.generate_stream(payload["prompt"]):
            yield json.dumps({"token": token}).encode() + b"\\n"
    """
    raise NotImplementedError(
        "Replace predict_stream() if streaming response was selected."
    )
