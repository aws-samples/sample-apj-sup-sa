"""Starlette server: Anthropic Messages API to Bedrock Converse API proxy."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import boto3
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from . import __version__
from .translate import (
    anthropic_image_to_bedrock,
    anthropic_to_converse,
    converse_stream_to_anthropic_events,
    converse_to_anthropic,
)

# Three log tiers, selected by BEDROCK_BRIDGE_LOG_LEVEL (set by the CLI):
#   default -> INFO   one access line per request, plus warnings/errors.
#   verbose -> DEBUG  adds internal adaptation detail (routing, vision-adapt,
#                     history-recall fixups, describe_image round counts).
#   debug   -> TRACE  adds request/response *content* (prompt text, full body
#                     and Converse kwargs). The CLI gates this behind an
#                     interactive consent prompt because it logs PII.
TRACE = 5
logging.addLevelName(TRACE, "TRACE")
_TIER_TO_LEVEL = {"default": logging.INFO, "verbose": logging.DEBUG, "debug": TRACE}
_tier = os.environ.get("BEDROCK_BRIDGE_LOG_LEVEL", "default").strip().lower()
_level = _TIER_TO_LEVEL.get(_tier, logging.INFO)  # unknown value -> quietest
# Root stays at INFO so third-party loggers (botocore especially, which dumps
# full signed requests including image bytes and auth material at DEBUG) are not
# dragged down with our tier. The handler basicConfig installs is NOTSET, so it
# still emits our logger's TRACE records; only our own logger's level varies.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("bedrock-bridge")
logger.setLevel(_level)


def _trace(msg: str | Callable[[], str]) -> None:
    """Log at TRACE (debug tier only). Carries request/response content.

    Accepts a zero-arg callable so expensive payload serialization
    (json.dumps / _scrub_bytes_only) is skipped unless TRACE is enabled.
    """
    if not logger.isEnabledFor(TRACE):
        return
    logger.log(TRACE, msg() if callable(msg) else msg)


_client = None
_region = None


def get_client() -> Any:
    global _client, _region
    if _client is None:
        from botocore.config import Config

        # Tag the User-Agent so bridge calls are identifiable in CloudTrail.
        ua = f"bedrock-bridge/{__version__}"
        # More generous read timeout than botocore's 60s default; keep connect
        # short so genuine network failures still fail fast.
        cfg = Config(
            user_agent=ua,
            connect_timeout=10,
            read_timeout=300,
        )
        # region_name=None lets boto3 resolve via its standard chain
        # (AWS_REGION env, AWS_DEFAULT_REGION, profile config, IMDS).
        _client = boto3.client("bedrock-runtime", config=cfg)
        _region = _client.meta.region_name
    return _client


_main_model: str | None = None
_light_model: str | None = None
# Optional side model used only to inspect images on behalf of a text-only
# main model (see _describe_images / messages). None means no vision side
# channel; image turns on a non-vision main model fall back to a text marker.
_vision_model: str | None = None
# Per-slot vision-capability flags. Default True so an unconfigured proxy
# does not strip images on a vision-capable model. The CLI sets these from
# its preflight result via /set-model.
_main_supports_vision: bool = True
_light_supports_vision: bool = True


def set_main_model(model_id: str) -> None:
    """Set the Bedrock model ID for primary requests."""
    global _main_model
    _main_model = model_id


def set_light_model(model_id: str | None) -> None:
    """Set the Bedrock model ID for light/background-helper requests."""
    global _light_model
    _light_model = model_id


def set_vision_model(model_id: str | None) -> None:
    """Set the Bedrock model ID used to inspect images for a text-only main."""
    global _vision_model
    _vision_model = model_id


def set_capabilities(main_vision: bool, light_vision: bool) -> None:
    global _main_supports_vision, _light_supports_vision
    _main_supports_vision = main_vision
    _light_supports_vision = light_vision


_IMAGE_CHIP_RE = re.compile(r"^\s*\[Image #\d+\]\s*$")
_LOST_IMAGE_PROMPT = (
    "[bedrock-bridge: an image was attached when this message was first sent, "
    "but Claude Code did not preserve the image bytes when this turn was "
    "recalled from history. Tell the user the image did not come through and "
    "ask them to re-attach it. Do NOT attempt to describe what was in the "
    "image; you cannot see it.]"
)


def _replace_lost_image_chips(body: dict) -> int:
    """Rewrite `[Image #N]` text chips to an explicit lost-image instruction
    when the enclosing message has no actual image content.

    Claude Code's history-recall path resends the chip text but drops the
    image bytes. Native Claude is good at inferring "I cannot see this" from
    just the chip; smaller open-weight models confabulate. This helper makes
    the lost-image situation explicit so any model can respond honestly.

    Mutates `body` in place. Returns count of chips rewritten. Live-paste
    turns (chip text plus a real image block in the same message) are left
    untouched.
    """
    rewritten = 0
    for msg in body.get("messages", []):
        if _msg_has_image(msg):
            continue  # real image present; chip text is just a label, leave alone
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for i, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text = block.get("text", "")
            if isinstance(text, str) and _IMAGE_CHIP_RE.match(text):
                content[i] = {"type": "text", "text": _LOST_IMAGE_PROMPT}
                rewritten += 1
    return rewritten


def _msg_has_image(msg: dict) -> bool:
    """True if this single message's content holds any image block."""
    content = msg.get("content")
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "image":
            return True
        if btype == "tool_result":
            inner = block.get("content")
            if isinstance(inner, list):
                for ib in inner:
                    if isinstance(ib, dict) and ib.get("type") == "image":
                        return True
    return False


def _has_image_content(body: dict) -> bool:
    """True if any message in the body holds an image block."""
    return any(_msg_has_image(m) for m in body.get("messages", []))


DESCRIBE_TOOL_NAME = "describe_image"

# Cap on describe_image rounds before the bridge stops calling the vision model
# and returns whatever the main model produced. Guards against a model that
# loops on describe_image forever. Generous: real turns rarely inspect more
# than a couple of images.
_MAX_DESCRIBE_ROUNDS = 8


def _image_handle(img_block: dict) -> str:
    """Short, opaque, content-derived handle for an image. Not a sequential
    index: indices are prompt-local and a result framed as "image #N is X"
    misleads the model into thinking it saw the image. Identical bytes map to
    the same handle, which is fine (same image, same description)."""
    raw = img_block.get("image", {}).get("source", {}).get("bytes", b"")
    if isinstance(raw, str):
        raw = raw.encode()
    return "img-" + hashlib.sha256(bytes(raw)).hexdigest()[:10]


def _no_vision_marker() -> dict:
    """Text block shown in place of an image when NO vision model is set.
    Guides the model to tell the user how to enable image support."""
    return {
        "type": "text",
        "text": (
            "[bedrock-bridge: an image was attached here, but this model has no "
            "vision and no --vision-model is configured, so the image cannot be "
            "inspected. Tell the user that to work with images they should restart "
            "bedrock-bridge with --vision-model <image-capable-model-id> (or set "
            "$BEDROCK_BRIDGE_MODEL_VISION), which routes images to that model via a "
            "describe_image tool. Do NOT suggest /model; model selection is fixed "
            "at startup. Do NOT attempt to describe the image; you cannot see it.]"
        ),
    }


def _describe_marker(handle: str) -> dict:
    """Text block shown in place of an image when a vision model IS set.
    Tells the main model the image is inspectable via the describe_image tool
    and how to address this specific image."""
    return {
        "type": "text",
        "text": (
            f"[bedrock-bridge: an image (handle: {handle}) was attached here. This "
            f"model has no vision, but a separate vision model is available. To see "
            f'the image, call the describe_image tool with image_handle="{handle}" '
            f"and a prompt stating what you need to know about it (e.g. what to "
            f"classify, debug, or extract). The tool returns a text description "
            f"only, not the image itself.]"
        ),
    }


def _map_image_blocks(body: dict, fn: Callable[[dict], dict]) -> int:
    """Walk every image block in the body (top-level and nested in
    tool_result) and replace it in place with fn(image_block). fn returns the
    replacement block. Returns the number of images replaced. Centralizes the
    two image positions so the marker/stash paths can't drift apart."""
    replaced = 0
    for msg in body.get("messages", []):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        new_content: list = []
        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue
            btype = block.get("type")
            if btype == "image":
                new_content.append(fn(block))
                replaced += 1
                continue
            if btype == "tool_result":
                inner = block.get("content")
                if isinstance(inner, list):
                    new_inner: list = []
                    for ib in inner:
                        if isinstance(ib, dict) and ib.get("type") == "image":
                            new_inner.append(fn(ib))
                            replaced += 1
                        else:
                            new_inner.append(ib)
                    block = {**block, "content": new_inner}
            new_content.append(block)
        msg["content"] = new_content
    return replaced


def _strip_images_from_body(body: dict) -> int:
    """Replace every image block with a text marker telling the user how to
    enable image support. Used when the main model has no vision AND no vision
    side model is configured. Forwarding (rather than refusing) keeps Claude
    Code's transcript from re-sending the same image forever. Mutates in place.
    """
    return _map_image_blocks(body, lambda _block: dict(_no_vision_marker()))


def _stash_images_for_describe(body: dict) -> dict[str, dict]:
    """Replace every image block with a describe_image marker and return a map
    of handle -> Bedrock image block, so the sub-loop can feed the real bytes
    to the vision model when the main model calls describe_image. Mutates body
    in place."""
    images: dict[str, dict] = {}

    def stash(block: dict) -> dict:
        bedrock_img = anthropic_image_to_bedrock(block)
        if bedrock_img is None:
            # Unconvertible source (e.g. URL, not base64); nothing to inspect.
            return dict(_no_vision_marker())
        handle = _image_handle(bedrock_img)
        images[handle] = bedrock_img
        return dict(_describe_marker(handle))

    _map_image_blocks(body, stash)
    return images


def _describe_tool_spec() -> dict:
    """Bedrock toolSpec for the bridge-internal describe_image tool. Injected
    into the main model's toolConfig only; Claude Code never sees or executes
    it. The bridge intercepts the call, runs the vision model, and answers it."""
    return {
        "toolSpec": {
            "name": DESCRIBE_TOOL_NAME,
            "description": (
                "Inspect an image that was attached to the conversation but "
                "cannot be seen by this model directly. A separate vision model "
                "looks at the image and returns a text description. Provide the "
                "image_handle from the bracketed bedrock-bridge marker, and a "
                "prompt describing what you need to know (what to classify, "
                "debug, read, or extract). The result is a text description "
                "only; you are not seeing the image yourself. You may call this "
                "tool multiple times on the same image_handle with different "
                "prompts to inspect different aspects; do so whenever you need "
                "detail you have not already obtained, rather than guessing."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "image_handle": {
                            "type": "string",
                            "description": "The handle from the [bedrock-bridge: ... handle: img-XXXXXX] marker.",
                        },
                        "prompt": {
                            "type": "string",
                            "description": (
                                "What to look for or answer about the image. Optional; omit for a general description."
                            ),
                        },
                    },
                    "required": ["image_handle"],
                }
            },
        }
    }


_VISION_SYSTEM_PROMPT = (
    "You are the eyes for another AI model that cannot see this image; it will "
    "act on your words as its only source of truth, so be concrete and "
    "complete. Always begin with one sentence naming what kind of image this is "
    "(for example: a photograph, a screenshot of a UI, a diagram, a document "
    "scan, a chart), then transcribe any prominent text verbatim, including "
    "non-English text. After that, answer the specific request. Describe only "
    "what is actually visible; if something asked for cannot be determined, say "
    "so plainly. Do not guess at intent or invent details that are not present."
)


def _call_vision_model(client: Any, image_block: dict, prompt: str | None) -> str:
    """Run the configured vision model on one image with an optional task
    prompt. Returns the model's text. Raises on Bedrock error so the caller can
    surface a clear tool_result instead of silently degrading."""
    user_text = prompt.strip() if (prompt and prompt.strip()) else ("Describe this image in detail.")
    resp = client.converse(
        modelId=_vision_model,
        system=[{"text": _VISION_SYSTEM_PROMPT}],
        messages=[
            {
                "role": "user",
                "content": [
                    image_block,
                    {"text": user_text},
                ],
            }
        ],
    )
    out = resp.get("output", {}).get("message", {}).get("content", [])
    parts = [b["text"] for b in out if isinstance(b, dict) and "text" in b]
    return "\n".join(parts).strip() or "(vision model returned no text)"


def _describe_result_text(prompt: str | None, handle: str, answer: str, ok: bool = True) -> str:
    """Frame a describe_image result around the prompt->answer pair, with
    explicit provenance that this is a second-hand text description and not the
    image itself. Avoids any "image #N is X" phrasing that would let the main
    model believe it saw the image."""
    asked = prompt.strip() if (prompt and prompt.strip()) else "a general description"
    if not ok:
        return f"[bedrock-bridge describe_image: could not inspect image {handle}. {answer}]"
    return (
        f"[bedrock-bridge describe_image: the vision model ({_vision_model}) "
        f'was asked for "{asked}" and returned the following. This is a '
        f"text description produced by another model, not the image itself, so "
        f"treat it as second-hand. The answer is scoped to that question and "
        f"may omit other details in the image; if you need a different aspect, "
        f"call describe_image again with a new prompt.\n\n{answer}]"
    )


def _strip_describe_blocks(response: dict) -> dict:
    """Remove any describe_image toolUse blocks from a Bedrock response before
    it is handed back to the caller (and on to Claude Code). describe_image is
    bridge-internal; Claude Code never registered it, so leaking such a block
    would leave the client trying to satisfy a tool it cannot run. Returns a
    response with those blocks dropped; other content is untouched."""
    out_msg = response.get("output", {}).get("message", {})
    blocks = out_msg.get("content", [])
    kept = [b for b in blocks if not (isinstance(b, dict) and b.get("toolUse", {}).get("name") == DESCRIBE_TOOL_NAME)]
    if len(kept) == len(blocks):
        return response
    new_msg = {**out_msg, "content": kept}
    return {**response, "output": {**response.get("output", {}), "message": new_msg}}


def _run_describe_loop(client: Any, model_id: str, kwargs: dict, metadata: dict, images: dict[str, dict]) -> dict:
    """Drive the main model non-streaming, intercepting describe_image tool
    calls. For each round: call main; if it asked for describe_image, answer
    those calls with the vision model and re-invoke; any non-describe tool_use
    in the same turn is discarded (the model re-decides with the descriptions
    now in context). Returns the first Bedrock response whose turn contains no
    describe_image call, with any describe_image block stripped. Caller converts
    it to Anthropic shape (JSON or SSE)."""
    kwargs = dict(kwargs)
    messages = list(kwargs.get("messages", []))
    kwargs["messages"] = messages
    # (handle, prompt) -> (result_text, is_error) for questions already answered.
    # Keyed by prompt too: a second, different question about the same image is
    # legitimately new work; only an identical re-request signals a loop.
    answered: dict[tuple[str, str | None], tuple[str, bool]] = {}

    for round_n in range(_MAX_DESCRIBE_ROUNDS):
        response = client.converse(modelId=model_id, **kwargs)
        blocks = response.get("output", {}).get("message", {}).get("content", [])

        describe_calls = [
            b["toolUse"]
            for b in blocks
            if isinstance(b, dict) and b.get("toolUse", {}).get("name") == DESCRIBE_TOOL_NAME
        ]
        if not describe_calls:
            # Main model produced its turn without asking to inspect any image.
            # This is the lazy contract working: the image is available via the
            # tool, but the model only calls it when it needs to.
            _trace(f"describe_image loop: round {round_n} produced no describe_image call; returning main turn")
            return response  # final turn; no describe_image to strip

        logger.debug(f"describe_image: round {round_n}, {len(describe_calls)} call(s)")
        _trace(
            lambda: (
                f"describe_image loop: round {round_n}, main model requested "
                f"{len(describe_calls)} describe_image call(s): "
                f"{[((tu.get('input') or {}).get('prompt') or '(no prompt)') for tu in describe_calls]}"
            )
        )

        # If every describe call this round repeats an identical, already-
        # answered question, the model is looping (typically because it also
        # wants a real tool we keep discarding). Stop and return this turn so
        # the real tool_use survives; strip the redundant describe blocks.
        def _key(tu: dict) -> tuple[str, str | None]:
            a = tu.get("input", {}) or {}
            return (a.get("image_handle", ""), a.get("prompt"))

        if all(_key(tu) in answered for tu in describe_calls):
            logger.debug("describe_image loop: round repeated only answered questions; returning")
            return _strip_describe_blocks(response)

        # Append the describe_image tool_uses as the assistant turn, then a user
        # turn with their results. Real (non-describe) tool_uses in this same
        # turn are dropped: we can't execute them, and forwarding a partial
        # tool-call turn to Claude Code would be invalid. The model re-decides
        # with the descriptions in context.
        messages.append({"role": "assistant", "content": [{"toolUse": tu} for tu in describe_calls]})

        result_blocks = []
        for tu in describe_calls:
            args = tu.get("input", {}) or {}
            handle = args.get("image_handle", "")
            prompt = args.get("prompt")
            key = (handle, prompt)
            if key in answered:
                text, is_error = answered[key]
            else:
                img = images.get(handle)
                if img is None:
                    text = _describe_result_text(
                        prompt,
                        handle or "(missing handle)",
                        "The image bytes are not available (the handle did not "
                        "match any attached image, or the image was not preserved "
                        "when this turn was recalled from history). Ask the user "
                        "to re-attach it.",
                        ok=False,
                    )
                    is_error = True
                else:
                    try:
                        _trace(f"describe_image: invoking vision model {_vision_model} for {handle}")
                        answer = _call_vision_model(client, img, prompt)
                        text = _describe_result_text(prompt, handle, answer)
                        is_error = False
                    except Exception as e:
                        logger.error(f"vision model call failed for {handle}: {e}")
                        text = _describe_result_text(
                            prompt,
                            handle,
                            f"The vision model returned an error: {e}",
                            ok=False,
                        )
                        is_error = True
                answered[key] = (text, is_error)
            result_blocks.append(
                {
                    "toolResult": {
                        "toolUseId": tu.get("toolUseId", ""),
                        "content": [{"text": text}],
                        **({"status": "error"} if is_error else {}),
                    }
                }
            )
        messages.append({"role": "user", "content": result_blocks})

    # Loop cap hit: one final call so the model can use the last description,
    # then strip any describe_image block so none leaks to the client.
    logger.warning(f"describe_image loop cap ({_MAX_DESCRIBE_ROUNDS}) reached")
    return _strip_describe_blocks(client.converse(modelId=model_id, **kwargs))


def _format_error(err: str, body: dict | None) -> tuple[int, str, str]:
    """Map a Bedrock error to a (status_code, error_type, message) tuple
    suitable for the Anthropic-shaped error envelope on the wire.

    Where a recovery path exists in Claude Code, rewrites the message to a
    substring it recognizes (e.g. "prompt is too long" triggers reactive
    compact; "image exceeds ... maximum" triggers stripImagesFromMessages).
    Always preserves the raw Bedrock error verbatim at the end for debugging.

    Substring matching is case-insensitive and status-agnostic in Claude Code,
    so a 400 + "prompt is too long" substring is enough to fire compact.
    """
    # Pattern provenance: each branch matches a verbatim Bedrock error string
    # observed from a real model. Bedrock collapses every validation failure
    # into `ValidationException` with no structured discriminator, so we have
    # to classify on the message text. See docs/error-mapping.md for the
    # catalog of observed samples, the model that produced each, and the date.
    # When a new model surfaces a phrasing these don't catch, add a sample
    # there and widen the keyword here, rather than keying patterns per model
    # ID (the category phrase is stable across models; the model ID is not).

    # Context window full -> Claude Code "prompt is too long" -> compact path.
    # Stable phrase across providers: "context length". Numbers are extracted
    # only to hand getPromptTooLongTokenGap a positive gap; magnitudes, not
    # exact values, are what matter.
    low = err.lower()
    if "context length" in low and ("exceed" in low or "maximum" in low):
        # Keep only large numbers; Mantle wrappers embed status codes
        # ("Some(400)") that would otherwise be mistaken for token counts.
        nums = [int(n) for n in re.findall(r"\d+", err) if int(n) >= 1000]
        limit, actual = (min(nums), max(nums)) if len(nums) >= 2 else (1, 2)
        message = (
            f"prompt is too long: {actual} tokens > {limit} maximum. "
            f"[bedrock-bridge] model context window exceeded. Raw: {err}"
        )
        return 400, "invalid_request_error", message

    # Requested output tokens exceed the model's per-request output cap.
    # Claude Code won't lower its own max_tokens, and Bedrock exposes no
    # per-model output cap to clamp at preflight, so there's no auto-recovery;
    # surface it plainly.
    if "maximum tokens you requested exceeds" in low:
        return (
            400,
            "invalid_request_error",
            (
                f"[bedrock-bridge] {err} The configured model caps output tokens "
                f"below what the client requested. Pick a model with a higher "
                f"output limit, or lower the client's max-tokens setting."
            ),
        )

    # Per-image size cap -> Claude Code's per-image strip-and-retry path.
    if "image exceeds" in err and "maximum" in err:
        return 413, "invalid_request_error", f"[bedrock-bridge] {err}"

    # Model-host body buffer cap (aggregate body, not a single image). Distinct
    # phrase from "context length"; maps to the same compact path. body_kb
    # feeds a synthetic token gap so compaction peels enough turns to fit.
    if "Failed to buffer the request body" in err or "length limit exceeded" in err:
        body_kb = 0
        if body is not None:
            try:
                body_kb = len(json.dumps(body)) // 1024
            except Exception:
                pass
        # Synthesize the "X tokens > Y maximum" pattern getAssistantMessageFromError
        # parses to drive compaction aggressiveness. Reporting bytes-as-tokens
        # is intentionally lenient: the regex only cares about magnitude. The
        # gap ensures Claude Code peels enough turns to fit, not just one.
        actual = max(body_kb * 250, 1)
        limit = max(actual - 1000, 1)
        message = (
            f"prompt is too long: {actual} tokens > {limit} maximum. "
            f"[bedrock-bridge] Bedrock model host buffer cap reached "
            f"(~{body_kb} KB request body). This is a per-model gateway cap, "
            f"separate from the model's context window. Common cause: large "
            f"tool_result blocks (screenshots, big file reads) accumulated "
            f"across turns. Raw: {err}"
        )
        return 400, "invalid_request_error", message

    # Default: pass through with the bridge prefix so users see where the
    # message originated, plus a pointer to the issue tracker. Claude Code
    # appends its own "server-side issue, check your inference gateway" tail
    # to 500s (hardcoded, not editable here), so we lead with the actionable
    # bit: this is likely a bridge translation gap, report it.
    return (
        500,
        "api_error",
        (
            f"[bedrock-bridge] {err} | If this looks like a bridge bug, report it: "
            f"https://github.com/prog893/bedrock-bridge/issues"
        ),
    )


def _route_supports_vision(model_id: str) -> bool:
    """Capability lookup for a routed Bedrock model ID."""
    if model_id == _light_model:
        return _light_supports_vision
    return _main_supports_vision


def _route(model_alias: str) -> str:
    """Pick the Bedrock model ID based on what the caller asked for.

    The CLI sets ANTHROPIC_MODEL=<main_id> and ANTHROPIC_DEFAULT_HAIKU_MODEL=<light_id>
    on the spawned Claude Code process, so the incoming `model` field is one
    of those two IDs verbatim. Exact match wins; "haiku" substring is the
    fallback for clients that emit Anthropic-style names without going through
    our env wiring.
    """
    if _light_model and model_alias == _light_model:
        return _light_model
    if _light_model and "haiku" in model_alias.lower():
        return _light_model
    if _main_model:
        return _main_model
    return model_alias


async def messages(request: Request) -> Response:
    body = await request.json()
    stream = body.get("stream", False)

    model_alias = body.get("model", "")
    model_id = _route(model_alias)

    raw_tools = body.get("tools", [])
    logger.info(f"-> model_in={model_alias} -> routed={model_id} stream={stream} tools={len(raw_tools)}")
    _trace(lambda: f"request body: {json.dumps(_scrub_bytes_only(body))}")
    # History-recall fixup: when Claude Code recalls a prior turn from
    # history, it resends the `[Image #N]` chip text but does not preserve
    # the image bytes. Native Claude reads the bare chip and refuses
    # gracefully; smaller open-weight models confabulate. Rewrite the chip
    # to an explicit instruction so any model can respond honestly. Skipped
    # for messages that still have a real image attached (live paste).
    n_lost = _replace_lost_image_chips(body)
    if n_lost:
        logger.debug(f"history-recall fixup: rewrote {n_lost} lost-image chip(s) to explicit instruction")

    # Vision adaptation: the routed model lacks IMAGE input modality but the
    # body carries images. Two paths, depending on whether a vision side model
    # is configured. In both cases we forward (never refuse): refusing returns
    # a 400 that corrupts Claude Code's transcript, since it retains the failed
    # user turn with the image and re-sends it on every subsequent turn, so we
    # would refuse forever.
    describe_images: dict[str, dict] = {}
    if not _route_supports_vision(model_id) and _has_image_content(body):
        if _vision_model:
            # Replace images with describe_image markers and stash the bytes.
            # The main model can call describe_image to have the side model
            # inspect them; the bridge answers that tool itself (see the loop).
            describe_images = _stash_images_for_describe(body)
            logger.debug(
                f"vision adapt: stashed {len(describe_images)} image(s) for "
                f"describe_image via vision model {_vision_model}"
            )
        else:
            # No side model: replace images with a marker telling the user how
            # to enable image support (restart with --vision-model).
            n = _strip_images_from_body(body)
            logger.debug(f"vision adapt: stripped {n} image block(s); no vision model set")

    converse_kwargs, metadata = anthropic_to_converse(body)
    metadata["model"] = model_alias
    _trace(lambda: f"converse_kwargs: {json.dumps(_scrub_bytes_only(converse_kwargs), default=str)}")
    client = get_client()

    # When describe_image is in play, inject its toolSpec into the main model's
    # toolConfig (Claude Code never sees this tool) so the model can call it.
    if describe_images:
        tool_cfg = converse_kwargs.setdefault("toolConfig", {"tools": []})
        tool_cfg["tools"] = list(tool_cfg.get("tools", [])) + [_describe_tool_spec()]

    try:
        if stream:
            return StreamingResponse(
                _stream_response(
                    client,
                    model_id,
                    converse_kwargs,
                    metadata,
                    body,
                    describe_images,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )
        else:
            if describe_images:
                response = _run_describe_loop(client, model_id, converse_kwargs, metadata, describe_images)
            else:
                response = client.converse(modelId=model_id, **converse_kwargs)
            result = converse_to_anthropic(response, metadata)
            _trace(lambda: f"response (json): {json.dumps(result)}")
            return JSONResponse(result)
    except Exception as e:
        err_str = str(e)
        logger.error(f"Bedrock error: {err_str}")
        # On validation errors, dump the incoming body + the outgoing Converse
        # kwargs so we can reproduce offline. Images are replaced with a
        # {bytes: <len>} marker to keep the dump small.
        if "ValidationException" in err_str:
            try:
                _dump_failure(body, converse_kwargs, err_str)
            except Exception as dump_err:
                logger.warning(f"failed to dump failure: {dump_err}")
        status, err_type, message = _format_error(err_str, body)
        return JSONResponse(
            {
                "type": "error",
                "error": {"type": err_type, "message": message},
            },
            status_code=status,
        )


async def _stream_response(
    client: Any,
    model_id: str,
    kwargs: dict,
    metadata: dict,
    body: dict | None = None,
    describe_images: dict[str, dict] | None = None,
) -> AsyncIterator[str]:
    """Call converse_stream and yield Anthropic SSE events.

    When describe_images is set, the describe_image sub-loop must run, and that
    loop is non-streaming (it inspects the assistant turn for tool calls before
    deciding whether to continue). In that case we run the loop, buffer the
    final message, and replay it as a synthetic SSE event sequence so the client
    still receives a stream. The common no-image path streams directly from
    Bedrock as before.
    """
    try:
        if describe_images:
            response = _run_describe_loop(client, model_id, kwargs, metadata, describe_images)
            _trace(lambda: f"buffered stream response: {json.dumps(_scrub_bytes_only(response), default=str)}")
            for chunk in _buffered_message_to_sse(response, metadata):
                yield chunk
            return

        _trace(f"stream start: model={model_id}")
        response = client.converse_stream(modelId=model_id, **kwargs)
        stream = response.get("stream", [])
        # Per-stream state for the translator (synthesizes content_block_start
        # events for indices Bedrock never opens explicitly).
        state: dict = {}

        for event in stream:
            for event_type, data in converse_stream_to_anthropic_events(event, metadata, state):
                yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

        yield "event: message_stop\ndata: {}\n\n"

    except Exception as e:
        err_str = str(e)
        logger.error(f"Stream error: {err_str}")
        if body is not None and "ValidationException" in err_str:
            try:
                _dump_failure(body, kwargs, err_str)
            except Exception as dump_err:
                logger.warning(f"failed to dump failure: {dump_err}")
        _, err_type, message = _format_error(err_str, body)
        payload = {"type": "error", "error": {"type": err_type, "message": message}}
        yield f"event: error\ndata: {json.dumps(payload)}\n\n"


def _sse(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _buffered_message_to_sse(response: dict, metadata: dict) -> Iterator[str]:
    """Replay a complete (non-streamed) Bedrock response as the Anthropic SSE
    event sequence Claude Code expects: message_start, then per content block a
    start/delta/stop, then message_delta + message_stop. Used after the
    describe_image sub-loop, which must buffer the turn to inspect it for tool
    calls before deciding to stream it. Reuses converse_to_anthropic so block
    shaping and tool-name restoration stay in one place."""
    msg = converse_to_anthropic(response, metadata)

    yield _sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                **{k: msg[k] for k in ("id", "type", "role", "model", "stop_sequence")},
                "content": [],
                "stop_reason": None,
                "usage": {"input_tokens": msg["usage"]["input_tokens"], "output_tokens": 0},
            },
        },
    )

    for idx, block in enumerate(msg["content"]):
        btype = block["type"]
        if btype == "text":
            yield _sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            yield _sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "text_delta", "text": block["text"]},
                },
            )
        elif btype == "thinking":
            yield _sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {"type": "thinking", "thinking": "", "signature": ""},
                },
            )
            yield _sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "thinking_delta", "thinking": block["thinking"]},
                },
            )
            if block.get("signature"):
                yield _sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {"type": "signature_delta", "signature": block["signature"]},
                    },
                )
        elif btype == "tool_use":
            yield _sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}},
                },
            )
            yield _sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])},
                },
            )
        elif btype == "redacted_thinking":
            yield _sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {"type": "redacted_thinking", "data": block["data"]},
                },
            )
        else:
            continue
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": idx})

    yield _sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": msg["stop_reason"], "stop_sequence": None},
            "usage": {
                "input_tokens": msg["usage"]["input_tokens"],
                "output_tokens": msg["usage"]["output_tokens"],
                "cache_read_input_tokens": msg["usage"].get("cache_read_input_tokens", 0),
                "cache_creation_input_tokens": msg["usage"].get("cache_creation_input_tokens", 0),
            },
        },
    )
    yield "event: message_stop\ndata: {}\n\n"


def _scrub_bytes_only(obj: Any) -> Any:
    """Redact image payloads but keep all text verbatim.

    Used for debug-tier content logging, where prompt text is logged in full on
    purpose. Contrast with _dump_failure's scrub(), which also truncates long
    strings; that would defeat the point of a debug dump.

    Two image shapes are redacted: raw bytes (Bedrock Converse, post-translation)
    and base64 strings in an Anthropic image source (`{"type": "base64",
    "data": "<base64>"}`, the incoming request shape before translation).
    """
    if isinstance(obj, dict):
        if obj.get("type") == "base64" and isinstance(obj.get("data"), str):
            return {
                **{k: _scrub_bytes_only(v) for k, v in obj.items() if k != "data"},
                "data": f"<redacted: {len(obj['data'])} base64 chars>",
            }
        return {k: _scrub_bytes_only(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_bytes_only(v) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        return f"<redacted: {len(obj)} bytes>"
    return obj


def _dump_failure(body: dict, kwargs: dict, err: str) -> None:
    """Persist a scrubbed copy of a failing request for offline debugging."""
    import datetime
    import tempfile
    import uuid

    def scrub(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: scrub(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [scrub(v) for v in obj]
        if isinstance(obj, (bytes, bytearray)):
            return {"__bytes_len__": len(obj), "__head_hex__": bytes(obj[:16]).hex()}
        if isinstance(obj, str) and len(obj) > 400:
            return obj[:200] + f"...<{len(obj)} chars truncated>"
        return obj

    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    path = os.path.join(tempfile.gettempdir(), f"bedrock-bridge-fail-{stamp}-{uuid.uuid4().hex[:6]}.json")
    with open(path, "w") as f:
        json.dump({"error": err, "body": scrub(body), "converse_kwargs": scrub(kwargs)}, f, indent=2, default=str)
    logger.error(f"dumped failing request to {path}")


async def set_model(request: Request) -> Response:
    body = await request.json()
    main = body.get("main_model_id") or body.get("model_id", "")
    light = body.get("light_model_id")
    vision = body.get("vision_model_id")
    main_vision = bool(body.get("main_supports_vision", True))
    light_vision = bool(body.get("light_supports_vision", True))
    set_main_model(main)
    set_light_model(light)
    set_vision_model(vision)
    set_capabilities(main_vision, light_vision)
    logger.info(
        f"Models set: main={main} (vision={main_vision}) "
        f"light={light or 'none'} (vision={light_vision}) "
        f"vision_model={vision or 'none'}"
    )
    return JSONResponse(
        {
            "status": "ok",
            "main_model_id": main,
            "light_model_id": light,
            "vision_model_id": vision,
            "main_supports_vision": main_vision,
            "light_supports_vision": light_vision,
        }
    )


async def health(request: Request) -> Response:
    return JSONResponse({"status": "ok"})


async def list_models(request: Request) -> Response:
    """Stub Anthropic models endpoint so Claude Code's discovery call passes."""
    created = "2025-01-01T00:00:00Z"
    items = []
    if _main_model:
        items.append({"id": _main_model, "display_name": _main_model, "type": "model", "created_at": created})
    if _light_model:
        items.append({"id": _light_model, "display_name": _light_model, "type": "model", "created_at": created})
    return JSONResponse({"data": items})


async def complete(request: Request) -> Response:
    """Handle legacy complete endpoint."""
    return JSONResponse(
        {"error": {"type": "not_supported", "message": "Use /v1/messages"}},
        status_code=400,
    )


app = Starlette(
    debug=False,
    routes=[
        Route("/v1/messages", messages, methods=["POST"]),
        Route("/v1/models", list_models, methods=["GET"]),
        Route("/v1/complete", complete, methods=["POST"]),
        Route("/set-model", set_model, methods=["POST"]),
        Route("/health", health, methods=["GET"]),
    ],
)
