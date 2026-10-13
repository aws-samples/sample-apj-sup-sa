"""Shared helpers for the Amazon Bedrock Mantle sample notebooks.

Import from a notebook with:

    import sys; sys.path.insert(0, "../_shared")
    from mantle import client, base_url, post, stream_lines, ttft

Everything here is deliberately small and dependency-light: the notebooks are the
teaching material, this file only removes repetition.

See 00-foundations/01-endpoints-auth-and-the-three-paths.ipynb for the full
explanation of auth, the three URL path families, and model discovery.

Style note: the SDK imports in token(), client() and anthropic_client() are
function-local on purpose, against the usual imports-at-top rule (PEP 8). This
module is imported by every notebook, including ones that never touch the OpenAI
or Anthropic SDK, and a function-local import keeps `import mantle` working when
only a subset of the optional SDKs is installed. The stdlib imports below follow
the normal convention.
"""

from __future__ import annotations

import ast
import json
import random  # retry jitter only -- never for tokens, keys, or nonces
import re
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
    """Return the regional bedrock-mantle endpoint origin (scheme + host)."""
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

# Observed behaviour: mantle sometimes reports a SERVER fault with a 4xx status and
# the body "Internal server error". Status alone therefore misclassifies it as a
# permanent client error, and a status-only retry policy gives up on a blip that
# succeeds immediately afterwards. Reproduced against a request that returned
# `400 Internal server error` once and then 200 on the next three attempts.
#
# So: retry a 4xx ONLY when the body says the server failed. Never widen this to
# all 400s -- a genuine "unsupported parameter" 400 must fail fast (S15-C17).
_SERVER_FAULT_TEXT = ("internal server error", "internal failure", "internal error")


def _is_retryable(status: int, payload: dict) -> bool:
    """True when this response is worth another attempt."""
    if status in _TRANSIENT:
        return True
    if 400 <= status < 500:
        message = str((payload.get("error") or {}).get("message") or "").lower()
        return any(marker in message for marker in _SERVER_FAULT_TEXT)
    return False


def _open_https(req: urllib.request.Request, timeout: int):
    """urlopen restricted to HTTPS.

    urllib honours file://, ftp:// and other schemes, so a URL that ever comes
    from data rather than from code could read a local file. Every call here is
    built from host() + a literal path, but the guard is cheap and keeps the
    property locally checkable (CWE-22 / Bandit B310).
    """
    if req.full_url.split("://", 1)[0] != "https":
        raise ValueError(f"refusing non-HTTPS URL: {req.full_url[:60]}")
    # Scheme verified https above; urllib's other schemes cannot be reached.
    # nosemgrep: dynamic-urllib-use-detected - scheme verified https above
    return urllib.request.urlopen(req, timeout=timeout)  # nosec B310  # noqa: S310


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
    RPM quota and sheds load under regional pressure. Also retries a 4xx whose body
    reports an internal server error - see _is_retryable. A genuine client error
    (unsupported parameter, unknown model) still fails on the first attempt.
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
            with _open_https(req, timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(raw) if raw.strip() else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                parsed = {"raw": raw[:500]}
            if _is_retryable(e.code, parsed) and attempt < attempts - 1:
                # Retry jitter, not a security decision.
                time.sleep(
                    min(2**attempt, 16) + random.random()  # nosec B311  # noqa: S311
                )
                continue
            return e.code, parsed
        except Exception as e:  # timeouts, connection resets
            if attempt < attempts - 1:
                # Retry jitter, not a security decision.
                time.sleep(
                    min(2**attempt, 16) + random.random()  # nosec B311  # noqa: S311
                )
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
    with _open_https(req, timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line:
                yield line


# Opaque service identifiers that appear in error text. They are not credentials,
# but they are long, high-entropy, and account-scoped: printing them in full adds
# nothing for a reader and trips secret scanners on committed notebook output.
_OPAQUE_ID = re.compile(r"\b((?:resp|msg|file|ft|proj|batch)[_-][A-Za-z0-9]{12,})\b")


def redact_ids(text: str, keep: int = 8) -> str:
    """Shorten opaque service IDs in a string, keeping enough to correlate a log.

    `resp_7jn3u5e6th46bypynamj6dc7rdoptjtjvmqf5bdlpe45e26phlfa`
        -> `resp_7jn3u5e6...`
    """

    def _shorten(m: re.Match) -> str:
        """Keep the type prefix and the first `keep` characters of the body."""
        token = m.group(1)
        prefix, _, body = token.partition("_")
        if not body:
            prefix, _, body = token.partition("-")
        return f"{prefix}_{body[:keep]}..." if body else token

    return _OPAQUE_ID.sub(_shorten, text or "")


# A 12-digit AWS account ID. 123456789012 is the documentation placeholder.
_ACCOUNT_ID = re.compile(r"(?<!\d)(?!123456789012)\d{12}(?!\d)")
_IAM_PRINCIPAL = re.compile(r"(:(?:user|role|assumed-role)/)[^\s\"',]+")


def redact_account(text: str) -> str:
    """Replace real account IDs and IAM principal names with placeholders.

    Notebook output is committed to a public repository, so anything printed here
    is published. An account ID is not a secret, but it identifies a real AWS
    account to anyone reading the samples and it trips content scanners. Call this
    on any string that may carry an ARN or a caller identity.

        arn:aws:iam::<your-account-id>:user/alice
            -> arn:aws:iam::123456789012:user/sample-user
    """
    out = _ACCOUNT_ID.sub("123456789012", text or "")
    return _IAM_PRINCIPAL.sub(r"\1sample-user", out)


def safe_print(*parts: object) -> None:
    """print() with account IDs and IAM principals redacted.

    Use it for anything derived from STS, an ARN, or a control-plane response.
    """
    print(*(redact_account(str(p)) for p in parts))


def err(payload: dict, limit: int = 160) -> str:
    """Pull the human-readable message out of an error body.

    Service error text often echoes back the ARN or ID you sent, so this redacts
    account IDs, IAM principals, and opaque IDs before returning. Notebook output is
    committed to a public repository; anything printed there is published.
    """
    e = payload.get("error") or {}
    msg = e.get("message") or e.get("code") or payload.get("raw") or json.dumps(payload)
    return redact_account(redact_ids(str(msg)))[:limit]


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
    """Group the model inventory by provider prefix, e.g. {"google": [...]}."""
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
    items = payload.get("output") or []
    return [i for i in items if i.get("type") == "function_call"]


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
    # Unclosed object - some models truncate tool-call arguments mid-object
    # (qwen3-coder does this reproducibly). Close the open braces and retry
    # once; that recovers the fields that did arrive.
    if depth > 0:
        patched = text[start:] + ('"' if in_string else "") + ("}" * depth)
        try:
            return json.loads(patched)
        except json.JSONDecodeError:
            pass
    raise ValueError(f"unbalanced JSON in: {text[:120]!r}")


def repair_tool_arguments(raw: str) -> str:
    """Return a JSON string that is safe to echo back to the API.

    Some models emit truncated tool-call arguments (e.g. `{"path": "x.py"` with no
    closing brace). Echoing that verbatim into the next request is rejected with a
    400. This re-serialises whatever parsed successfully.
    """
    try:
        return json.dumps(parse_json_lenient(raw or "{}"))
    except ValueError:
        return "{}"


# ---------------------------------------------------------------------------
# Inspecting model-generated code SAFELY
#
# A coding model returns source text. It is tempting to exec() it to prove it
# works - do not. Model output is untrusted input (OWASP LLM05), and a notebook
# kernel holds your live AWS credentials, so exec() there is arbitrary code
# execution against your own account. It is also unnecessary: everything worth
# checking about generated code can be checked statically.
#
# To actually RUN generated code you need real isolation - a container or
# microVM with no credentials, no network, and a CPU/memory cap. AWS Lambda in a
# dedicated account, or Bedrock AgentCore's code-interpreter tool, both give you
# that. Running it in this kernel does not.
# ---------------------------------------------------------------------------
def extract_code_block(markdown: str) -> str:
    """Return the first fenced code block from a model response.

    Falls back to the whole string when the model answered without fences.
    """
    text = markdown or ""
    if "```" not in text:
        return text.strip()
    block = text.split("```")[1]
    first_newline = block.find("\n")
    if first_newline != -1 and " " not in block[:first_newline].strip():
        block = block[first_newline + 1:]  # drop the language tag
    return block.strip()


def inspect_code(source: str) -> dict:
    """Statically analyse generated Python. Never executes it.

    Returns a dict describing what the code declares:

        parses     bool  - is it syntactically valid Python?
        error      str   - the SyntaxError message when it is not
        functions  dict  - {name: [parameter names]} for each top-level def
        classes    list  - top-level class names
        imports    list  - modules the code would import
        raises     list  - exception type names in `raise` statements
        calls      list  - names of functions the code calls

    Use it to assert that the model met a specification - the right function
    name, the right parameters, the required guard clause - without ever
    handing control to the generated text.
    """
    out: dict = {"parses": False, "error": "", "functions": {}, "classes": [],
                 "imports": [], "raises": [], "calls": []}
    try:
        tree = ast.parse(source or "")
    except SyntaxError as exc:
        out["error"] = f"line {exc.lineno}: {exc.msg}"
        return out

    out["parses"] = True
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in node.args.args]
            args += [a.arg for a in node.args.kwonlyargs]
            out["functions"][node.name] = args
        elif isinstance(node, ast.ClassDef):
            out["classes"].append(node.name)
        elif isinstance(node, ast.Import):
            out["imports"] += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out["imports"].append((node.module or "").split(".")[0])
        elif isinstance(node, ast.Raise):
            exc_node = node.exc
            name = getattr(exc_node, "id", None) or getattr(
                getattr(exc_node, "func", None), "id", None
            )
            if name:
                out["raises"].append(name)
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name:
                out["calls"].append(name)
    return out


def check_spec(source: str, *, function: str, params: list[str] | None = None,
               raises: str | None = None) -> dict:
    """Score generated code against a specification, statically.

    Returns {"parses", "defines", "signature", "guard", "ok"} - each a bool
    except the reason string. `params` is the expected parameter-name list;
    `raises` an exception type the code must raise somewhere.
    """
    info = inspect_code(source)
    defines = function in info["functions"]
    signature = defines and (params is None or info["functions"][function] == params)
    guard = raises is None or raises in info["raises"]
    return {
        "parses": info["parses"],
        "defines": defines,
        "signature": signature,
        "guard": guard,
        "ok": info["parses"] and defines and signature and guard,
        "reason": info["error"] or ("" if defines else f"no def {function}"),
    }


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
