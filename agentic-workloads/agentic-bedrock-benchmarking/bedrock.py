"""Thin wrappers around boto3 Bedrock control-plane and runtime APIs."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

BEDROCK_REGIONS: list[str] = [
    "us-east-1",
    "us-east-2",
    "us-west-2",
    "ca-central-1",
    "sa-east-1",
    "eu-west-1",
    "eu-west-2",
    "eu-west-3",
    "eu-central-1",
    "eu-north-1",
    "ap-south-1",
    "ap-northeast-1",
    "ap-northeast-2",
    "ap-northeast-3",
    "ap-southeast-1",
    "ap-southeast-2",
]

# Converse-supported attachment formats
IMAGE_FORMATS = {"png", "jpeg", "jpg", "gif", "webp"}
DOCUMENT_FORMATS = {"pdf", "csv", "doc", "docx", "xls", "xlsx", "html", "htm", "txt", "md"}


@dataclass(frozen=True)
class ModelEntry:
    id: str
    display_name: str
    provider: str
    kind: str  # "foundation" | "profile"
    input_modalities: tuple[str, ...] = ()    # e.g. ("TEXT", "IMAGE")
    output_modalities: tuple[str, ...] = ()

    @property
    def supports_image(self) -> bool:
        return "IMAGE" in self.input_modalities

    @property
    def supports_document(self) -> bool:
        # Bedrock doesn't expose a "DOCUMENT" modality in list_foundation_models.
        # In practice, Converse accepts document blocks for the same set of models that accept image
        # blocks (the major ones: Claude 3+, Nova, Llama 3.2 Vision, Mistral Large, etc.).
        # We mirror image support as a conservative proxy; if a model rejects, the user sees the error.
        return self.supports_image


@dataclass
class Attachment:
    """Image or document blob to attach to a Converse call."""
    name: str
    fmt: str        # one of IMAGE_FORMATS or DOCUMENT_FORMATS
    data: bytes
    kind: str       # "image" | "document"


@dataclass
class InvokeResult:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int                            # full end-to-end
    ttft_ms: Optional[int] = None              # time to first visible token
    tpot_ms: Optional[float] = None            # mean ms per output token after first
    stop_reason: Optional[str] = None          # "end_turn" | "max_tokens" | "stop_sequence" | etc.
    error: Optional[str] = None

    @property
    def truncated(self) -> bool:
        return self.stop_reason == "max_tokens"

    @property
    def output_tps(self) -> Optional[float]:
        """Output tokens per second during generation (excludes TTFT)."""
        if self.tpot_ms and self.tpot_ms > 0:
            return 1000.0 / self.tpot_ms
        return None


def get_caller_identity() -> dict:
    sts = boto3.client("sts")
    return sts.get_caller_identity()


def list_models(region: str) -> list[ModelEntry]:
    """Return active, on-demand, text-out foundation models plus inference profiles."""
    bedrock = boto3.client("bedrock", region_name=region)
    entries: list[ModelEntry] = []

    fm_resp = bedrock.list_foundation_models()
    for m in fm_resp.get("modelSummaries", []):
        if m.get("modelLifecycle", {}).get("status") != "ACTIVE":
            continue
        if "ON_DEMAND" not in m.get("inferenceTypesSupported", []):
            continue
        in_mods = tuple(m.get("inputModalities", []))
        out_mods = tuple(m.get("outputModalities", []))
        if "TEXT" not in in_mods:
            continue
        if "TEXT" not in out_mods:
            continue
        entries.append(
            ModelEntry(
                id=m["modelId"],
                display_name=f"{m.get('providerName', '?')} / {m.get('modelName', m['modelId'])}",
                provider=m.get("providerName", "?"),
                kind="foundation",
                input_modalities=in_mods,
                output_modalities=out_mods,
            )
        )

    try:
        prof_resp = bedrock.list_inference_profiles()
        for p in prof_resp.get("inferenceProfileSummaries", []):
            if p.get("status") != "ACTIVE":
                continue
            # Inference profiles don't expose modalities directly. Infer from the underlying model id.
            in_mods: tuple[str, ...] = ("TEXT",)
            for fm in p.get("models", []) or []:
                fm_id = (fm.get("modelArn") or "").rsplit("/", 1)[-1]
                # Lookup in the FM list we just built.
                for entry in entries:
                    if entry.id == fm_id:
                        in_mods = entry.input_modalities
                        break
            entries.append(
                ModelEntry(
                    id=p["inferenceProfileId"],
                    display_name=f"[profile] {p.get('inferenceProfileName', p['inferenceProfileId'])}",
                    provider="inference-profile",
                    kind="profile",
                    input_modalities=in_mods,
                    output_modalities=("TEXT",),
                )
            )
    except (ClientError, BotoCoreError):
        pass

    entries.sort(key=lambda e: (e.kind, e.provider.lower(), e.display_name.lower()))
    return entries


def _normalize_format(fmt: str, kind: str) -> str:
    f = fmt.lower().lstrip(".")
    if f == "jpg":
        f = "jpeg"
    if f == "htm":
        f = "html"
    valid = IMAGE_FORMATS if kind == "image" else DOCUMENT_FORMATS
    if f not in valid:
        raise ValueError(f"unsupported {kind} format: {fmt}")
    return f


def _doc_name_safe(name: str) -> str:
    """Converse requires document names to match [a-zA-Z0-9\\-_\\(\\) \\[\\]]+ and be 1-64 chars."""
    stem = name.rsplit(".", 1)[0]
    cleaned = re.sub(r"[^a-zA-Z0-9\-_()\[\] ]", "_", stem)[:64].strip()
    return cleaned or "document"


def _build_content_blocks(prompt: str, attachments: list[Attachment]) -> list[dict]:
    blocks: list[dict] = []
    for att in attachments:
        if att.kind == "image":
            blocks.append({
                "image": {
                    "format": _normalize_format(att.fmt, "image"),
                    "source": {"bytes": att.data},
                }
            })
        elif att.kind == "document":
            blocks.append({
                "document": {
                    "name": _doc_name_safe(att.name),
                    "format": _normalize_format(att.fmt, "document"),
                    "source": {"bytes": att.data},
                }
            })
    blocks.append({"text": prompt})
    return blocks


def invoke(
    region: str,
    model_id: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    attachments: Optional[list[Attachment]] = None,
    on_chunk: Optional[Callable[[str], None]] = None,
) -> InvokeResult:
    """Streaming Converse call. Captures TTFT/TPOT.

    `on_chunk` (optional) is called with each text delta as it arrives — used by the UI to
    render tokens live. Errors are caught and returned on the result.
    """
    runtime = boto3.client("bedrock-runtime", region_name=region)
    content_blocks = _build_content_blocks(prompt, attachments or [])

    started = time.perf_counter()
    first_token_t: Optional[float] = None
    text_parts: list[str] = []
    stop_reason: Optional[str] = None
    input_tokens = 0
    output_tokens = 0

    try:
        resp = runtime.converse_stream(
            modelId=model_id,
            messages=[{"role": "user", "content": content_blocks}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
        )
        for event in resp.get("stream", []):
            if "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta", {})
                chunk = delta.get("text")
                if chunk:
                    if first_token_t is None:
                        first_token_t = time.perf_counter()
                    text_parts.append(chunk)
                    if on_chunk is not None:
                        try:
                            on_chunk(chunk)
                        except Exception:  # noqa: BLE001 — UI callback failures must not break the call
                            pass
            elif "messageStop" in event:
                stop_reason = event["messageStop"].get("stopReason")
            elif "metadata" in event:
                usage = event["metadata"].get("usage", {}) or {}
                input_tokens = int(usage.get("inputTokens", 0))
                output_tokens = int(usage.get("outputTokens", 0))

        ended = time.perf_counter()
        latency_ms = int((ended - started) * 1000)
        ttft_ms = int((first_token_t - started) * 1000) if first_token_t else None

        # TPOT = mean inter-token gap after the first token.
        tpot_ms: Optional[float] = None
        if first_token_t and output_tokens > 1:
            generation_ms = (ended - first_token_t) * 1000
            tpot_ms = generation_ms / max(1, output_tokens - 1)

        return InvokeResult(
            text="".join(text_parts),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            tpot_ms=tpot_ms,
            stop_reason=stop_reason,
        )
    except (ClientError, BotoCoreError) as e:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return InvokeResult(
            text="".join(text_parts),
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            error=str(e),
        )
