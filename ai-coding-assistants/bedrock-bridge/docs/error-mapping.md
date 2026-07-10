# Error mapping catalog

Bedrock returns request-validation failures as a single `ValidationException`
with no machine-readable discriminator. To give Claude Code a recoverable
experience, the bridge classifies these by matching the human-readable message
text and rewrites them to strings Claude Code's recovery paths recognize
(`server._format_error`).

This file records the provenance of those patterns: the verbatim error samples
observed, which model produced each, and what the bridge does with them. When a
model surfaces a phrasing the current keywords miss, add the sample here and
widen the keyword in `_format_error`, rather than keying patterns per model ID.
The category phrase ("context length", "image exceeds", etc.) is stable across
models; the model ID is not a useful key.

None of these are faults of the model or of Claude Code. They are normal limits
(context size, per-request output cap, payload size) being reached. The bridge
sits in the middle and has no channel to tell Claude Code a given model's
context size or output cap ahead of time, so it translates the resulting error
into a form Claude Code already knows how to handle.

Samples below are the inner message Bedrock returned, sometimes wrapped in a
Mantle envelope like `Mantle streaming error ... ErrorEvent { error: APIError {
... message: "<inner>" ... } }`.

## Context window reached

**Match:** message contains `context length` and (`exceed` or `maximum`).
**Rewrite:** `400` + `prompt is too long: <actual> tokens > <limit> maximum`.
**Effect:** Claude Code matches `prompt is too long` and runs its compact path
(prune old turns), or shows "Context limit reached, /compact or /clear".

Observed:

| Model | Date | Verbatim inner message |
|-------|------|------------------------|
| `qwen.qwen3-coder-480b-a35b-v1:0` | 2026-06-04 | `This model's maximum context length is 131072 tokens. However, you requested 32000 output tokens and your prompt contains at least 99073 input tokens, for a total of at least 131073 tokens.` |
| `qwen.qwen3-32b-v1:0` | 2026-06-04 | `This model's maximum context length is 32768 tokens. However, you requested 32000 output tokens and your prompt contains 123421 characters (more than ...).` |
| `openai.gpt-oss-120b-1:0` | 2026-06-04 | `Input length (131075) exceeds model's maximum context length (131072).` |

Number extraction keeps only values >= 1000 so Mantle status codes
(`Some(400)`) are not mistaken for token counts. The exact numbers only tune how
aggressively Claude Code compacts; any positive gap works.

## Output token cap reached

**Match:** message contains `maximum tokens you requested exceeds`.
**Rewrite:** `400` + verbatim + guidance (pick a higher-output model or lower
the client's max-tokens). No auto-recovery: Claude Code does not lower its own
`max_tokens`, and Bedrock exposes no per-model output cap to clamp at preflight.

Observed:

| Model | Date | Verbatim message |
|-------|------|------------------|
| `amazon.nova-pro-v1:0` (via `apac.` profile) | 2026-06-04 | `The maximum tokens you requested exceeds the model limit of 10000. Try again with a maximum tokens value that is lower than 10000.` |
| `amazon.nova-lite-v1:0` | 2026-06-04 | `The maximum tokens you requested exceeds the model limit of 10000. Try again with a maximum tokens value that is lower than 10000.` |

## Per-image size cap reached

**Match:** message contains `image exceeds` and `maximum`.
**Rewrite:** `413` + verbatim. Routes through Claude Code's per-image
strip-and-retry path.

Observed:

| Model | Date | Verbatim message |
|-------|------|------------------|
| Claude on Bedrock (direct probe) | 2026-06-04 | `messages.N.content.M.image.source.base64: image exceeds 5 MB maximum: 6557392 bytes > 5242880 bytes` |

## Model-host body buffer cap reached

**Match:** message contains `Failed to buffer the request body` or
`length limit exceeded`.
**Rewrite:** `400` + synthetic `prompt is too long` with a token gap derived
from the request body size in KB. Same compact path as context-full, but the
trigger is aggregate request body size at the model host, not the model's
context window.

Observed:

| Model | Date | Verbatim inner message |
|-------|------|------------------------|
| `moonshotai.kimi-k2.5` | 2026-06-04 | `Failed to buffer the request body: length limit exceeded` |

Common trigger: many `tool_result` blocks (parallel screenshots, large file
reads) accumulated across turns.

## Tool name / use-ID charset and length

Not handled in `_format_error`; normalized upstream in `translate.py` before
the request is sent. Bedrock constraints:

- `toolSpec.name`: `[a-zA-Z0-9_-]+`, max 64 chars.
- `toolUse(Result).toolUseId`: `[a-zA-Z0-9_.:-]+`, max 64 chars.

Observed:

| Model | Date | Verbatim value |
|-------|------|----------------|
| `moonshotai.kimi-k2.5` | 2026-06-04 | tool-use ID `functions.toolu_01YfH6Uz9RzXBpimzKn5wbUS <|tool_call_ar...` (chat-template tokens appeared in the ID: spaces and `<|...`) |

The shortener rewrites any value that is too long or contains characters
outside the allowed set, deterministically, so the `toolUse` and its matching
`toolResult` resolve to the same value and restore on the response leg.

## Default (unclassified)

Anything not matched above returns `500` + `[bedrock-bridge] <verbatim> | If
this looks like a bridge bug, report it: <issues URL>`. Claude Code renders
this as an `API Error` and appends its own "server-side issue / check your
inference gateway" tail. An unclassified error usually means a new bridge
translation gap worth filing.
