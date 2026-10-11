"""SageMaker inference handler for OmniVoice zero-shot TTS.

Runs in the PyTorch GPU DLC. A custom handler is required because OmniVoice is
not a transformers pipeline - its own library loads the checkpoint and exposes a
generate() that needs a reference clip.

WHY THE PYTORCH DLC AND NOT THE HUGGINGFACE ONE
omnivoice requires transformers>=5.3.0 and the HuggingFace DLC ships 4.51.3.
Upgrading a major version of transformers inside a container whose serving
toolkit itself depends on it is a good way to get a container that installs
cleanly and then fails its health check. Nothing in the PyTorch DLC depends on
transformers, so here it is just another package.

THE ENDPOINT RETURNS 8kHz, NOT 24kHz
OmniVoice synthesises at 24kHz and the PSTN leg is 8kHz narrowband. The
downsample happens HERE rather than on the Asterisk box for two reasons: the
media host is a 2GB t4g.small already running VAD, STT and the call pipeline, and
sending 24kHz over the wire would be three times the payload for audio that is
about to be thrown away. The resample uses resample_poly, not slicing - naive
decimation folds the 4-12kHz content back into the passband as aliasing, which on
a phone line sounds like harsh sibilance.

SPEAKING RATE IS SET HERE, NOT BY RE-RECORDING THE REFERENCE
A caller reported the agent was "too fast" and the first instinct was to fix it in
the reference clip - read it slower, and since the clone tracks its reference the
agent slows down too. That works, but it is the wrong lever: it burns a re-record
for something generate() already exposes as a parameter, and it couples the voice's
IDENTITY (whose voice, what timbre) to its PACE, so you cannot change one without
redoing the other. OmniVoice takes `speed` (>1 faster, <1 slower) and `duration`
(fixed seconds, overrides speed). Pace is configuration; the reference is identity.

LANGUAGE SELECTS THE REFERENCE
Upstream guidance is explicit that cross-lingual cloning imports an accent: "use a
reference audio in the same language as the target speech... the generated speech
will carry an accent from the reference audio's language". This agent speaks en-IN,
hi-IN and Hinglish, so a single reference cannot be neutral for all three - one
English clip means Hindi turns come out anglicised.

So the reference is selected per language, with the default as fallback:

    ref.wav / ref.txt                  default, required
    ref-en.wav / ref-en.txt            optional override
    ref-hi.wav / ref-hi.txt            optional override
    ref-hinglish.wav / ref-hinglish.txt

Shipping fewer than all three is fine and is the expected starting state - every
language falls back to the default until its own clip exists. Keeping ONE SPEAKER
across all three matters more than covering all three: a Hinglish call switches
language mid-sentence, and the caller must not hear the person change.

`language` was previously accepted in the request and then silently dropped, so
every language got the English reference with no indication in the logs.

WHY CAPABILITIES ARE INTROSPECTED
`speed`, `num_step`, `normalize_text` and the precomputed-prompt API arrived across
different omnivoice releases, and this container pins one. Passing an unsupported
keyword to generate() would fail every request, which is a bad way to find out. So
the signature is inspected once at load and unsupported keywords are dropped with a
warning. A stale pin degrades to the old behaviour instead of a dead endpoint, and
CloudWatch says which knobs the container actually has.

Contract
    POST  ContentType: application/json
          {"text": "...", "language": "en", "speed": 0.86}
            text      required
            language  en | hi | hinglish, selects the reference clip. Optional.
            speed     optional per-request override, for tuning. Bounded.
          Accept:      audio/pcm          -> raw 8kHz signed 16-bit LE mono
          Accept:      application/json   -> {"audio": "<base64>", "sampleRate": 8000,
                                              "referenceUsed": "...", "speed": ...,
                                              "wordsPerMinute": ...}
"""
from __future__ import annotations

import base64
import inspect
import io
import json
import logging
import os
import time

import numpy as np
import torch

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("omnivoice-handler")

NATIVE_RATE = 24000   # what OmniVoice produces
TARGET_RATE = 8000    # what the telephone leg needs
DECIMATION = NATIVE_RATE // TARGET_RATE  # exactly 3, so no rational approximation

MODEL_ID = os.environ.get("OMNIVOICE_MODEL_ID", "k2-fsa/OmniVoice")

# Speaking rate. Conversational speech is 140-160 wpm; the first clone measured 199
# and a caller volunteered that it was too fast.
#
# PER LANGUAGE, because the clone tracks its reference and the references do not read
# at the same pace: measured 159 wpm for the English human clip, 191 for the Polly
# Hindi one, 173 for Hinglish. One global factor would have to be wrong for at least
# one language - correct Hindi would drag English to a crawl and vice versa.
#
# Treat cross-language wpm comparisons as rough. Word counts are not commensurable
# between English and Devanagari, so these are starting points to be checked by ear,
# not derived constants.
SPEED = float(os.environ.get("OMNIVOICE_SPEED", "1.0"))
SPEED_BY_LANGUAGE = {
    lang: float(os.environ[var])
    for lang, var in (("en", "OMNIVOICE_SPEED_EN"),
                      ("hi", "OMNIVOICE_SPEED_HI"),
                      ("hinglish", "OMNIVOICE_SPEED_HINGLISH"))
    if os.environ.get(var)
}


def _speed_for(language: str) -> float:
    return SPEED_BY_LANGUAGE.get(language, SPEED)

# Diffusion steps. Upstream documents 32 as the quality setting and 16 as the fast
# one. The turn budget is already over target, so this is the first dial to try if
# TTS latency needs to come down - but it trades audio quality, so it is measured,
# not assumed.
NUM_STEP = int(os.environ.get("OMNIVOICE_NUM_STEP", "32"))

# A medical-history form is mostly numbers - height, weight, BP, sugar, years. Left
# alone, "162" can be read digit by digit. normalize_text turns numerals into words
# before synthesis. Needs the [tn] extra for English; other languages fall back to
# num2words, so Hindi numerals are worth listening to before trusting this.
NORMALIZE_TEXT = os.environ.get("OMNIVOICE_NORMALIZE_TEXT", "1") not in ("0", "false", "")

# Mirrors common/hvi_state.normalise_language. Duplicated because the inference
# container ships only this directory - it has no access to common/.
_LANGUAGE_ALIASES = {
    "en-in": "en", "en_in": "en", "en": "en", "english": "en",
    "hi-in": "hi", "hi_in": "hi", "hi": "hi", "hindi": "hi",
    "hinglish": "hinglish", "hi-en": "hinglish", "en-hi": "hinglish",
}


def _normalise_language(value: str | None) -> str:
    """Map any CRM or carrier spelling onto en / hi / hinglish."""
    if not value:
        return "hinglish"
    return _LANGUAGE_ALIASES.get(str(value).strip().lower(), "hinglish")


def _load_references(model_dir: str) -> dict:
    """
    Collect the default reference plus any per-language overrides.

    The default is mandatory: zero-shot TTS has no built-in voice, so a missing
    reference is not something to discover on the first call.
    """
    default_wav = os.path.join(model_dir, "ref.wav")
    default_txt = os.path.join(model_dir, "ref.txt")
    if not os.path.exists(default_wav) or not os.path.exists(default_txt):
        raise FileNotFoundError(
            f"reference voice missing: expected {default_wav} and {default_txt}. "
            "OmniVoice is zero-shot and cannot synthesise without a reference."
        )

    refs: dict[str, dict] = {}
    with open(default_txt, encoding="utf-8") as fh:
        refs["default"] = {"audio": default_wav, "text": fh.read().strip()}

    for lang in ("en", "hi", "hinglish"):
        wav = os.path.join(model_dir, f"ref-{lang}.wav")
        txt = os.path.join(model_dir, f"ref-{lang}.txt")
        if os.path.exists(wav) and os.path.exists(txt):
            with open(txt, encoding="utf-8") as fh:
                refs[lang] = {"audio": wav, "text": fh.read().strip()}
            log.info("reference for %s: %s", lang, os.path.basename(wav))
        else:
            log.info("no %s reference; falling back to the default (expect a "
                     "reference-language accent on %s output)", lang, lang)
    return refs


def model_fn(model_dir: str):
    """Load the model and every reference voice once, at container start."""
    from omnivoice import OmniVoice  # noqa: PLC0415 - installed at container build

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device.startswith("cuda") else torch.float32
    log.info("loading %s on %s (%s)", MODEL_ID, device, dtype)

    started = time.time()
    model = OmniVoice.from_pretrained(MODEL_ID, device_map=device, dtype=dtype)
    log.info("model loaded in %.1fs", time.time() - started)

    # What does THIS build of generate() actually accept? See the module docstring.
    try:
        signature = inspect.signature(model.generate)
        caps = set(signature.parameters)
        # Generation-config fields (num_step, guidance_scale, ...) travel
        # through **kwargs -> OmniVoiceGenerationConfig.from_dict, so they are
        # real knobs that never appear as named parameters. Gating them on the
        # named-parameter set silently dropped num_step on every request - the
        # endpoint ran the config default (32 steps) regardless of
        # OMNIVOICE_NUM_STEP, which made the latency knob a no-op. If generate()
        # takes **kwargs, num_step is supported.
        if any(param.kind is inspect.Parameter.VAR_KEYWORD
               for param in signature.parameters.values()):
            caps.add("num_step")
    except (TypeError, ValueError):
        # Signature unavailable (C extension or a decorator without functools.wraps).
        # Assume only the documented minimum so nothing unsupported is sent.
        caps = {"text", "ref_audio", "ref_text"}
        log.warning("could not introspect generate(); assuming the minimal API")
    log.info("generate() supports: speed=%s num_step=%s normalize_text=%s "
             "voice_clone_prompt=%s",
             "speed" in caps, "num_step" in caps, "normalize_text" in caps,
             "voice_clone_prompt" in caps)
    log.info("speed: default %.2f, per-language %s", SPEED, SPEED_BY_LANGUAGE or "{}")
    for name, wanted in (("speed", SPEED != 1.0 or bool(SPEED_BY_LANGUAGE)),
                         ("normalize_text", NORMALIZE_TEXT)):
        if wanted and name not in caps:
            log.warning("%s was requested but this omnivoice build does not accept "
                        "it; ignoring. Bump the pin in requirements.txt to use it.",
                        name)

    ctx = {"model": model, "refs": _load_references(model_dir), "caps": caps,
           "prompts": {}}

    # Encode each reference once instead of re-encoding it on every request. The
    # reference never changes, so doing this per call is pure repeated work on the
    # critical path of a live phone turn.
    if "voice_clone_prompt" in caps and hasattr(model, "create_voice_clone_prompt"):
        for key, ref in ctx["refs"].items():
            try:
                started = time.time()
                ctx["prompts"][key] = model.create_voice_clone_prompt(
                    ref_audio=ref["audio"], ref_text=ref["text"])
                log.info("precomputed voice prompt for %s in %.2fs",
                         key, time.time() - started)
            except Exception:
                # Not fatal - the ref_audio/ref_text path still works, just slower.
                log.exception("could not precompute the %s voice prompt; will pass "
                              "ref_audio per request", key)

    # Warm the graph, and use the warmup as a live probe of the options that can
    # fail at CALL time rather than at import time.
    #
    # normalize_text is the one that matters: accepting the keyword only proves the
    # signature has it, not that WeTextProcessing/num2words is installed, and the
    # failure would otherwise land on a caller mid-sentence. So the warmup text
    # contains a number, and if that raises, normalize_text is dropped for the life
    # of the container and the endpoint serves without it.
    warm = "Hello, this is a warm up utterance covering 162 centimetres."
    try:
        started = time.time()
        _synthesise(ctx, warm)
        log.info("warmup generate in %.2fs", time.time() - started)
    except Exception:
        if "normalize_text" in ctx["caps"] and NORMALIZE_TEXT:
            log.exception("warmup failed with normalize_text enabled; retrying "
                          "without it (numerals will be read as digits)")
            ctx["caps"] = set(ctx["caps"]) - {"normalize_text"}
            try:
                _synthesise(ctx, warm)
                log.warning("warmup succeeded without normalize_text. Install the "
                            "extra (omnivoice[tn]) to read numbers as words.")
            except Exception:
                # Still failing, so it was never about text normalisation. A failed
                # warmup must not stop the endpoint coming up: the real call may
                # work, and refusing to serve turns a slow first turn into a dead
                # endpoint.
                log.exception("warmup failed without normalize_text too; continuing")
        else:
            log.exception("warmup failed; continuing")

    return ctx


def _synthesise(ctx: dict, text: str, language: str = "default",
                speed_override: float | None = None,
                num_step_override: int | None = None) -> np.ndarray:
    """Generate float32 audio at NATIVE_RATE using the reference for `language`."""
    caps = ctx["caps"]
    key = language if language in ctx["refs"] else "default"
    kwargs: dict = {"text": text}

    prompt = ctx["prompts"].get(key)
    if prompt is not None:
        kwargs["voice_clone_prompt"] = prompt
    else:
        ref = ctx["refs"][key]
        kwargs["ref_audio"] = ref["audio"]
        kwargs["ref_text"] = ref["text"]

    # Only send what this build understands; see the module docstring.
    speed = speed_override if speed_override is not None else _speed_for(language)
    if "speed" in caps and speed != 1.0:
        kwargs["speed"] = speed
    if "num_step" in caps:
        kwargs["num_step"] = (num_step_override if num_step_override is not None
                              else NUM_STEP)
    if "normalize_text" in caps and NORMALIZE_TEXT:
        kwargs["normalize_text"] = True

    audio = ctx["model"].generate(**kwargs)
    # Documented as a list of np.ndarray shape (T,) at 24kHz.
    first = audio[0] if isinstance(audio, (list, tuple)) else audio
    return np.asarray(first, dtype=np.float32)


def _to_telephony_pcm(audio: np.ndarray) -> bytes:
    """24kHz float -> 8kHz signed 16-bit little-endian."""
    from scipy.signal import resample_poly  # noqa: PLC0415

    if audio.ndim > 1:
        audio = audio.mean(axis=0)

    # up=1, down=3 with the toolkit's anti-alias filter. Exact integer ratio, so
    # no phase drift over a long utterance.
    down = resample_poly(audio, up=1, down=DECIMATION)

    # Clip BEFORE the int16 cast. Diffusion vocoders can overshoot slightly past
    # 1.0, and an unclipped cast wraps around into loud clicks rather than simply
    # clipping - a very audible defect on a quiet phone line.
    np.clip(down, -1.0, 1.0, out=down)
    return (down * 32767.0).astype("<i2").tobytes()


# A per-request speed override has to be bounded. Outside roughly this range the
# audio stops being speech, and an unclamped value from a caller-facing path is a way
# to make the agent unintelligible with one bad config push.
SPEED_LIMITS = (0.5, 2.0)

# Per-request diffusion-step override, bounded for the same reason as speed.
# Upstream documents 32 as the quality setting and 16 as the fast one; below 8
# the output degrades into noise and above 64 is pure waste on a phone line.
# This exists so a latency/quality sweep costs seconds per attempt instead of a
# 15-25 minute endpoint update - the same economics that justified the speed
# override. The winning value gets baked into OMNIVOICE_NUM_STEP afterwards.
NUM_STEP_LIMITS = (8, 64)


def input_fn(body, content_type: str = "application/json") -> dict:
    if content_type and "json" not in content_type:
        raise ValueError(f"unsupported ContentType {content_type}; send application/json")
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8")
    data = json.loads(body) if isinstance(body, str) else body
    text = (data.get("text") or "").strip()
    if not text:
        raise ValueError("'text' is required and must be non-empty")

    # Optional per-request speed, for TUNING.
    #
    # It exists because the alternative is unusable: SPEED is read from the
    # environment at container start, so finding the right value by redeploying costs
    # a 15-25 minute endpoint update per attempt. Nobody iterates at that price, which
    # means the value would be guessed once and left. With this, a sweep takes
    # seconds and the winning number gets baked into the endpoint config afterwards.
    if data.get("speed") is not None:
        try:
            requested = float(data["speed"])
        except (TypeError, ValueError):
            raise ValueError(f"'speed' must be a number, got {data['speed']!r}")
        lo, hi = SPEED_LIMITS
        if not (lo <= requested <= hi):
            raise ValueError(f"'speed' must be within {lo}-{hi}, got {requested}")
        data["speed"] = requested

    # Optional per-request diffusion steps, for TUNING - same economics as speed.
    if data.get("num_step") is not None:
        try:
            steps = int(data["num_step"])
        except (TypeError, ValueError):
            raise ValueError(f"'num_step' must be an integer, got {data['num_step']!r}")
        lo, hi = NUM_STEP_LIMITS
        if not (lo <= steps <= hi):
            raise ValueError(f"'num_step' must be within {lo}-{hi}, got {steps}")
        data["num_step"] = steps
    return data


def predict_fn(data: dict, ctx: dict) -> dict:
    text = data["text"].strip()
    language = _normalise_language(data.get("language"))
    served_by = language if language in ctx["refs"] else "default"
    speed_override = data.get("speed")
    speed_used = speed_override if speed_override is not None else _speed_for(language)
    num_step_override = data.get("num_step")
    steps_used = num_step_override if num_step_override is not None else NUM_STEP

    started = time.time()
    audio = _synthesise(ctx, text, language, speed_override, num_step_override)
    gen_ms = int((time.time() - started) * 1000)

    pcm = _to_telephony_pcm(audio)
    secs = len(pcm) / 2 / TARGET_RATE
    rtf = (gen_ms / 1000.0 / secs) if secs else 0.0
    wpm = (len(text.split()) / secs * 60) if secs else 0.0
    # wpm is logged because speaking rate is a live product complaint, and a log
    # line per turn is how a regression gets noticed before a caller reports it.
    log.info("synthesised %d chars [%s -> ref:%s speed:%.2f%s steps:%d%s] -> "
             "%.2fs audio in %dms (rtf %.3f, %.0f wpm)",
             len(text), language, served_by, speed_used,
             " (per-request)" if speed_override is not None else "",
             steps_used,
             " (per-request)" if num_step_override is not None else "",
             secs, gen_ms, rtf, wpm)
    return {"pcm": pcm, "seconds": secs, "generateMs": gen_ms,
            "language": language, "referenceUsed": served_by,
            "wordsPerMinute": round(wpm), "speed": speed_used,
            "numStep": steps_used}


def output_fn(prediction: dict, accept: str = "audio/pcm"):
    pcm = prediction["pcm"]
    if accept and "json" in accept:
        payload = json.dumps({
            "audio": base64.b64encode(pcm).decode("ascii"),
            "sampleRate": TARGET_RATE,
            "seconds": prediction["seconds"],
            "generateMs": prediction["generateMs"],
            # Echoed so a diagnostic can assert which reference served the turn and
            # what rate came out, without parsing CloudWatch.
            "language": prediction.get("language"),
            "referenceUsed": prediction.get("referenceUsed"),
            "wordsPerMinute": prediction.get("wordsPerMinute"),
            "speed": prediction.get("speed"),
            "numStep": prediction.get("numStep"),
        })
        return payload, "application/json"
    # Raw PCM is the hot path: the media pipeline chunks these bytes straight
    # into 20ms AudioSocket frames with no decode step.
    return pcm, "audio/pcm"
