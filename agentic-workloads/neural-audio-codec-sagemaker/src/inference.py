"""
SageMaker serving code — multi-codec neural audio endpoint.
Serves Mimi / EnCodec behind one endpoint; pick via {"codec": ...}.

SageMaker hooks: model_fn (once) -> input_fn -> predict_fn -> output_fn.

Request JSON:
  {"codec": "mimi"|"encodec",
   "task": "encode"|"decode"|"roundtrip",
   "audio_b64": <wav>,            # for encode/roundtrip
   "codes": [[...]],              # for decode
   "num_quantizers": 8}           # optional
"""
import io
import json
import base64
import time

import torch
import librosa
import soundfile as sf
from transformers import AutoFeatureExtractor, MimiModel, EncodecModel

# native sample rate per codec
SR = {"mimi": 24000, "encodec": 24000}

# Pin model weights to a specific commit so a re-push upstream cannot silently
# change the served weights. Find a hash on the model's Hugging Face page under
# "Files and versions"; for production, pre-bake the weights into model.tar.gz.
REVISION = {
    "mimi": "89091b3e466eb6a9d11e537bf26b144f194978f7",
    "encodec": "c1dbe2ae3f1de713481a3b3e7c47f357092ee040",
}

# request validation bounds
VALID_CODECS = ("mimi", "encodec")
VALID_TASKS = ("encode", "decode", "roundtrip")
MAX_QUANTIZERS = 32
# EnCodec only supports these codebook counts (mapped to its bandwidth settings)
ENCODEC_QUANTIZERS = (2, 4, 8, 16, 32)


def model_fn(model_dir):
    """Load both codecs ONCE at container start, pinned to a fixed revision."""
    codecs = {
        "mimi": (
            AutoFeatureExtractor.from_pretrained("kyutai/mimi", revision=REVISION["mimi"]),
            MimiModel.from_pretrained("kyutai/mimi", revision=REVISION["mimi"]).eval(),
        ),
        "encodec": (
            AutoFeatureExtractor.from_pretrained("facebook/encodec_24khz", revision=REVISION["encodec"]),
            EncodecModel.from_pretrained("facebook/encodec_24khz", revision=REVISION["encodec"]).eval(),
        ),
    }
    return codecs


def input_fn(body, content_type):
    if content_type == "application/json":
        return json.loads(body)
    raise ValueError(f"Unsupported content type: {content_type}")


def _validate(data):
    """Reject malformed requests with a clear message before any model call."""
    codec = data.get("codec", "mimi")
    if codec not in VALID_CODECS:
        raise ValueError(f"codec must be one of {VALID_CODECS}, got {codec!r}")

    task = data.get("task", "roundtrip")
    if task not in VALID_TASKS:
        raise ValueError(f"task must be one of {VALID_TASKS}, got {task!r}")

    nq = data.get("num_quantizers", 8)
    try:
        nq = int(nq)
    except (TypeError, ValueError):
        raise ValueError(f"num_quantizers must be an integer, got {nq!r}")
    if not 1 <= nq <= MAX_QUANTIZERS:
        raise ValueError(f"num_quantizers must be between 1 and {MAX_QUANTIZERS}, got {nq}")
    if codec == "encodec" and nq not in ENCODEC_QUANTIZERS:
        raise ValueError(f"encodec num_quantizers must be one of {ENCODEC_QUANTIZERS}, got {nq}")

    if task in ("encode", "roundtrip"):
        b64 = data.get("audio_b64")
        if not isinstance(b64, str) or not b64:
            raise ValueError("audio_b64 (base64-encoded WAV) is required for encode/roundtrip")
        try:
            # binascii.Error (raised on bad base64) is a subclass of ValueError
            raw = base64.b64decode(b64, validate=True)
        except ValueError:
            raise ValueError("audio_b64 is not valid base64")
        if not raw:
            raise ValueError("audio_b64 decoded to empty bytes")
    else:
        raw = None

    codes_t = None
    if task == "decode":
        if "codes" not in data:
            raise ValueError("codes is required for decode")
        try:
            codes_t = torch.tensor(data["codes"], dtype=torch.long)
        except (TypeError, ValueError, RuntimeError):
            raise ValueError("codes must be a rectangular 2D array of integers (shape [codebooks, frames])")
        if codes_t.ndim != 2 or codes_t.shape[0] < 1 or codes_t.shape[1] < 1:
            raise ValueError(f"codes must be a non-empty 2D array [codebooks, frames], got shape {list(codes_t.shape)}")

    return codec, task, nq, raw, codes_t


def _encode(codec, fe, model, audio, nq):
    """Return (codes_tensor (1,cb,frames), frames, elapsed_ms)."""
    sr = SR[codec]
    inp = fe(raw_audio=audio, sampling_rate=sr, return_tensors="pt")
    t0 = time.perf_counter()
    with torch.no_grad():
        if codec == "mimi":
            codes = model.encode(inp["input_values"], num_quantizers=nq).audio_codes
        else:  # encodec
            # EnCodec maps codebook count -> bandwidth (nq 2/4/8/16/32 = 1.5/3/6/12/24 kbps).
            # Without an explicit bandwidth, transformers silently uses the LOWEST (2 cb).
            EC_BW = {2: 1.5, 4: 3.0, 8: 6.0, 16: 12.0, 32: 24.0}
            bw = EC_BW.get(nq, 6.0)
            enc = model.encode(inp["input_values"], bandwidth=bw)
            codes = enc.audio_codes.squeeze(0)          # (1, cb, frames)
    ms = (time.perf_counter() - t0) * 1000
    return codes, codes.shape[-1], ms


def _decode(codec, model, codes):
    """Return (wav_np, elapsed_ms)."""
    t0 = time.perf_counter()
    with torch.no_grad():
        if codec == "encodec":
            wav = model.decode(codes.unsqueeze(0), [None])[0]
        else:  # mimi
            wav = model.decode(codes)[0]
    ms = (time.perf_counter() - t0) * 1000
    return wav.squeeze().numpy(), ms


def predict_fn(data, ctx):
    codec, task, nq, raw, codes_t = _validate(data)
    fe, model = ctx[codec]
    sr = SR[codec]

    codes = None
    enc_ms = dec_ms = None
    frames = None

    if task in ("encode", "roundtrip"):
        audio, _ = librosa.load(io.BytesIO(raw), sr=sr, mono=True)
        codes, frames, enc_ms = _encode(codec, fe, model, audio, nq)
        dur = len(audio) / sr
        if task == "encode":
            return {
                "codec": codec, "shape": list(codes.shape),
                "frames": frames, "duration_s": round(dur, 3),
                "frames_per_sec": round(frames / dur, 2),
                "encode_ms": round(enc_ms, 1),
                "codes": codes.squeeze(0).tolist(),
            }

    if task == "decode":
        codes = codes_t.unsqueeze(0)

    wav, dec_ms = _decode(codec, model, codes)
    buf = io.BytesIO()
    sf.write(buf, wav, sr, format="WAV")
    out = {
        "codec": codec,
        "audio_b64": base64.b64encode(buf.getvalue()).decode(),
        "decode_ms": round(dec_ms, 1),
        "shape": list(codes.shape),
    }
    if enc_ms is not None:
        out["encode_ms"] = round(enc_ms, 1)
        out["frames"] = frames
    return out


def output_fn(pred, accept):
    from sagemaker_inference import encoder
    return encoder.encode(pred, accept or "application/json")
