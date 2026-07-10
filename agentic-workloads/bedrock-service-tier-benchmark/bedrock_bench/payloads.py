"""Build request payloads in the schema each (transport, model) expects.

We bypass llmeter's ``create_payload`` helpers because we need precise control
of the body shape per transport/style (verified against live Bedrock), and we
want the *same* logical prompt rendered identically every call so token counts
stay constant.
"""

from __future__ import annotations

from typing import Any

from .config import PayloadStyle


def build_invoke_payload(style: PayloadStyle, prompt: str, max_tokens: int) -> dict[str, Any]:
    """Body for ``bedrock-runtime`` InvokeModelWithResponseStream.

    ``modelId`` and ``serviceTier`` are *not* included here — the llmeter
    endpoint adapter owns those as top-level boto3 kwargs.
    """
    if style is PayloadStyle.NOVA:
        # Bedrock Converse-native body. Verified chunk stream: messageStart /
        # contentBlockDelta{delta.text} / contentBlockStop / metadata.usage.
        return {
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": max_tokens},
        }
    # OpenAI ChatCompletions-like body. Verified chunk stream:
    # choices[0].delta.content (+ a convenient top-level service_tier field).
    return {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }


def build_mantle_payload(prompt: str, max_tokens: int) -> dict[str, Any]:
    """Extra (non-streaming-flag) kwargs for the OpenAI ChatCompletions create call.

    The llmeter ``OpenAICompletionStreamEndpoint`` injects ``model``, ``stream``
    and ``stream_options`` in ``prepare_payload``; we supply messages + cap.
    ``service_tier`` is added by the adapter via ``extra_body``.
    """
    return {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
