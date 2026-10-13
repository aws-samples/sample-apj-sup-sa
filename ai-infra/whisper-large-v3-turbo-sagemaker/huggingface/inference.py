"""
Custom SageMaker inference handler for openai/whisper-large-v3-turbo.

The HuggingFace Inference Toolkit calls these four functions in order:
    model_fn      -> load the model once when the container starts
    input_fn      -> deserialize the incoming request body
    predict_fn    -> run inference
    output_fn     -> serialize the response

Supported request content types:
    audio/*           raw audio bytes (wav, mp3, flac, m4a, ogg, ...)
    application/json  {"audio": "<base64 audio>", optional params...}
"""

import base64
import io
import json
import logging
import os

import torch
from transformers import pipeline

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

MODEL_ID = "openai/whisper-large-v3-turbo"


def model_fn(model_dir, context=None):
    """Load the ASR pipeline once per container.

    Loads the weights baked into the model artifact (model_dir) when present,
    so no download from the Hugging Face Hub happens at container start. Falls
    back to the Hub only if the artifact is missing the weights.
    """
    device = 0 if torch.cuda.is_available() else -1
    torch_dtype = torch.float16 if torch.cuda.is_available() else torch.float32

    if os.path.exists(os.path.join(model_dir, "config.json")):
        model_source = model_dir
        logger.info("Loading model from baked-in artifact: %s", model_dir)
    else:
        model_source = MODEL_ID
        logger.warning("No weights found in %s; falling back to Hub download of %s",
                       model_dir, MODEL_ID)

    logger.info("cuda=%s dtype=%s", torch.cuda.is_available(), torch_dtype)

    asr = pipeline(
        task="automatic-speech-recognition",
        model=model_source,
        torch_dtype=torch_dtype,
        device=device,
        # Long-form transcription via chunking so audio > 30s works out of the box.
        chunk_length_s=30,
        batch_size=8,
    )
    return asr


def input_fn(request_body, request_content_type):
    """Turn the raw request body into bytes + generation params."""
    params = {}

    if request_content_type and request_content_type.startswith("audio/"):
        audio_bytes = request_body
        if isinstance(audio_bytes, str):
            audio_bytes = audio_bytes.encode("utf-8")
        return {"audio": audio_bytes, "params": params}

    if request_content_type == "application/json":
        payload = json.loads(request_body)
        if "audio" not in payload:
            raise ValueError("JSON payload must include a base64-encoded 'audio' field.")
        audio_bytes = base64.b64decode(payload["audio"])

        # Optional generation controls.
        for key in ("language", "task", "return_timestamps"):
            if key in payload:
                params[key] = payload[key]
        return {"audio": audio_bytes, "params": params}

    raise ValueError(
        f"Unsupported content type: {request_content_type}. "
        "Use an audio/* type for raw bytes or application/json with a base64 'audio' field."
    )


def predict_fn(data, model):
    """Run transcription."""
    audio_bytes = data["audio"]
    params = data.get("params", {})

    generate_kwargs = {}
    if "language" in params:
        generate_kwargs["language"] = params["language"]
    if "task" in params:
        generate_kwargs["task"] = params["task"]  # "transcribe" or "translate"

    call_kwargs = {}
    if generate_kwargs:
        call_kwargs["generate_kwargs"] = generate_kwargs
    if params.get("return_timestamps"):
        call_kwargs["return_timestamps"] = params["return_timestamps"]

    result = model(io.BytesIO(audio_bytes).read(), **call_kwargs)
    return result


def output_fn(prediction, accept="application/json"):
    """Return the prediction object; the toolkit serializes it to JSON."""
    return prediction
