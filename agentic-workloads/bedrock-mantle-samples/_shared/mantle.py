"""Shared helpers for the Amazon Bedrock Mantle sample notebooks.

Import from a notebook with:

    import sys; sys.path.insert(0, "../_shared")
    from mantle import client, base_url, post, stream_lines, ttft

Everything here is deliberately small and dependency-light: the notebooks are the
teaching material, this file only removes repetition.

See 00-foundations/01-endpoints-auth-and-the-three-paths.ipynb for the full
explanation of auth, the three URL path families, and model discovery.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request

DEFAULT_REGION = "us-east-1"

# ---------------------------------------------------------------------------
# The three path families on bedrock-mantle.
#
#   /openai/v1/*        google gemma-4, openai gpt-5.x, xai grok
#   /v1/*               openai gpt-oss + every Chat-Completions-only family
#   /anthropic/v1/*     anthropic claude only
#
# Control-plane paths (models, files, projects, fine-tuning, data retention)
# always live under /v1/*, never /openai/v1/*.
# ---------------------------------------------------------------------------
_OPENAI_PREFIX_FAMILIES = ("google.gemma-4", "openai.gpt-5", "xai.")


def api_prefix(model_id: str) -> str:
    """Return "/openai/v1" or "/v1" for a model's inference paths."""
    if model_id.startswith("anthropic."):
        return "/anthropic/v1"
    if any(model_id.startswith(p) for p in _OPENAI_PREFIX_FAMILIES):
        return "/openai/v1"
    return "/v1"


def host(region: str = DEFAULT_REGION) -> str:
    return f"https://bedrock-mantle.{region}.api.aws"


def base_url(model_id: str, region: str = DEFAULT_REGION) -> str:
    """Base URL to hand to the OpenAI SDK for this model."""
    return host(region) + api_prefix(model_id)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def token(region: str = DEFAULT_REGION) -> str:
    """Short-term Bedrock API key minted from the ambient IAM credentials.

    Expires in <=12h and cannot be refreshed - mint a new one instead. See
    00-foundations/01 for the self-refreshing provider and the SigV4 alternative.
    """
    from aws_bedrock_token_generator import provide_token

    return provide_token(region=region)


def client(model_id: str, region: str = DEFAULT_REGION):
    """An OpenAI SDK client pointed at the right base URL for this model."""
    from openai import OpenAI

    return OpenAI(api_key=token(region), base_url=base_url(model_id, region))


def anthropic_client(region: str = DEFAULT_REGION):
    """An Anthropic SDK client pointed at bedrock-mantle."""
    import anthropic

    return anthropic.Anthropic(
        api_key=token(region), base_url=host(region) + "/anthropic"
    )


# ---------------------------------------------------------------------------
# Raw HTTP with retries - used where the SDKs don't reach (control plane,
# Anthropic beta headers, deliberately-invalid requests that must show a 400).
# ---------------------------------------------------------------------------
_TRANSIENT = {429, 500, 502, 503, 504}


def post(
    path: str,
    body: dict | None,
    *,
    region: str = DEFAULT_REGION,
    headers: dict | None = None,
    method: str = "POST",
    attempts: int = 5,
    timeout: int = 240,
) -> tuple[int, dict]:
    """Signed-by-bearer-token JSON call. Returns (status_code, parsed_body).

    Never raises on HTTP errors: 4xx/5xx come back as (code, error_body) so the
    notebooks can *show* the error rather than blowing up the kernel.
    Retries 429 and 5xx with exponential backoff + jitter, because mantle has no
    RPM quota and sheds load under regional pressure.
    """
    url = host(region) + path
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(attempts):
        hdrs = {
            "Authorization": f"Bearer {token(region)}",
            "Content-Type": "application/json",
        }
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(raw) if raw.strip() else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                parsed = {"raw": raw[:500]}
            if e.code in _TRANSIENT and attempt < attempts - 1:
                time.sleep(min(2**attempt, 16) + random.random())
                continue
            return e.code, parsed
        except Exception as e:  # timeouts, connection resets
            if attempt < attempts - 1:
                time.sleep(min(2**attempt, 16) + random.random())
                continue
            return -1, {"error": {"message": f"{type(e).__name__}: {e}"}}
    return -1, {"error": {"message": "retries exhausted"}}


def stream_lines(path: str, body: dict, *, region: str = DEFAULT_REGION,
                 headers: dict | None = None, timeout: int = 240):
    """Yield raw SSE lines from a streaming endpoint (no SDK)."""
    hdrs = {
        "Authorization": f"Bearer {token(region)}",
        "Content-Type": "application/json",
    }
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(
        host(region) + path, data=json.dumps(body).encode(), headers=hdrs, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line:
                yield line


def err(payload: dict, limit: int = 160) -> str:
    """Pull the human-readable message out of an error body."""
    e = payload.get("error") or {}
    msg = e.get("message") or e.get("code") or payload.get("raw") or json.dumps(payload)
    return str(msg)[:limit]


# ---------------------------------------------------------------------------
# Small conveniences used across notebooks
# ---------------------------------------------------------------------------
def list_models(region: str = DEFAULT_REGION) -> list[str]:
    """Model inventory. NOTE: only /v1/models works - /openai/v1/models is 404."""
    code, payload = post("/v1/models", None, region=region, method="GET")
    if code != 200:
        raise RuntimeError(f"list_models failed {code}: {err(payload)}")
    return sorted(m["id"] for m in payload.get("data", []))


def families(region: str = DEFAULT_REGION) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for mid in list_models(region):
        out.setdefault(mid.split(".")[0], []).append(mid)
    return out


def response_text(payload: dict) -> str:
    """Extract assistant text from a Responses API payload.

    Prefers the top-level output_text, falls back to walking output[] - the
    Responses API returns reasoning/tool items alongside the message.
    """
    if isinstance(payload.get("output_text"), str) and payload["output_text"]:
        return payload["output_text"]
    parts = []
    for item in payload.get("output", []) or []:
        if item.get("type") == "message":
            for block in item.get("content", []) or []:
                if block.get("text"):
                    parts.append(block["text"])
    return "".join(parts)


def function_calls(payload: dict) -> list[dict]:
    """Function-call items from a Responses payload."""
    return [i for i in (payload.get("output") or []) if i.get("type") == "function_call"]


def parse_json_lenient(text: str) -> dict:
    """Parse the first complete JSON object out of model output.

    Some models append trailing characters after a well-formed object even in
    "strict" structured-output mode (Gemma 4 does this intermittently - see
    03-google-gemma/01). Plain json.loads() then raises even though the useful
    payload is intact. This walks braces to find the first balanced object and
    parses that.
    """
    text = (text or "").strip()
    if text.startswith("```"):  # strip markdown fences if present
        text = text.split("```")[1] if "```" in text[3:] else text.lstrip("`")
        text = text[4:] if text.startswith("json") else text
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start == -1:
        raise ValueError(f"no JSON object found in: {text[:120]!r}")
    depth, in_string, escaped = 0, False, False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : idx + 1])
    raise ValueError(f"unbalanced JSON in: {text[:120]!r}")


def ttft(path: str, body: dict, *, region: str = DEFAULT_REGION,
         headers: dict | None = None, timeout: int = 120) -> dict:
    """Time a streaming call: time-to-first-token and output frames/sec.

    Counts SSE data frames as a proxy for tokens - good enough to compare
    service tiers and models against each other, not an exact token count.

    Never raises: a request that a model rejects (e.g. an unsupported
    service_tier) or that stalls returns an "error" key instead, so a
    benchmarking loop over many models/tiers always completes.
    """
    body = {**body, "stream": True}
    start = time.perf_counter()
    first = None
    frames = 0
    try:
        for line in stream_lines(path, body, region=region, headers=headers,
                                 timeout=timeout):
            if not line.startswith("data:"):
                continue
            if line.strip() == "data: [DONE]":
                break
            frames += 1
            if first is None:
                first = time.perf_counter() - start
    except urllib.error.HTTPError as e:
        return {"ttft_s": 0.0, "total_s": 0.0, "frames": 0, "frames_per_s": 0.0,
                "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ttft_s": 0.0, "total_s": 0.0, "frames": 0, "frames_per_s": 0.0,
                "error": type(e).__name__}
    total = time.perf_counter() - start
    gen = max(total - (first or 0), 1e-6)
    return {
        "ttft_s": round(first or 0, 3),
        "total_s": round(total, 3),
        "frames": frames,
        "frames_per_s": round(frames / gen, 1),
    }
