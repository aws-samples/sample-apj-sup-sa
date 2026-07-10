"""llmeter ``Endpoint`` adapters that add flex-tier support.

llmeter measures TTFT / total latency correctly from streaming deltas, but its
stock Bedrock endpoints hard-code the boto3 kwargs (there is literally a
``# TODO: ... serviceTier`` in ``bedrock_invoke.py``) and its OpenAI endpoint has
no service-tier concept. These two subclasses add exactly that, while reusing
llmeter's parsing/timing machinery.

Both adapters stash the **served** tier (what Bedrock actually used, which can
differ from what we requested) onto the returned ``InvocationResponse`` as a
plain ``served_tier`` attribute. ``InvocationResponse`` is a non-frozen
dataclass; the attribute is read by the scheduler immediately after ``invoke``.
"""

from __future__ import annotations

import json
import logging
import time

from llmeter.endpoints import BedrockInvokeStream, OpenAICompletionStreamEndpoint
from llmeter.endpoints.base import Endpoint

from .auth import AuthBroker

logger = logging.getLogger("bedrock_bench.endpoints")

# Bedrock returns the served tier in this response header (and sometimes a
# top-level serviceTier key). Verified against live us-west-2 / us-east-1.
_SERVED_TIER_HEADER = "x-amzn-bedrock-service-tier"

# jmespath presets for the two InvokeModel stream shapes (verified empirically).
# nosec B106 below: these are JMESPath query strings for extracting token *counts*
# from responses, not credentials — bandit's heuristic misfires on the arg name.
JMESPATH_OPENAI = dict(
    generated_text_jmespath="choices[0].delta.content",
    generated_token_count_jmespath='"amazon-bedrock-invocationMetrics".outputTokenCount',  # nosec B106
    input_text_jmespath="messages[].content[].text",
    input_token_count_jmespath='"amazon-bedrock-invocationMetrics".inputTokenCount',
)
JMESPATH_NOVA = dict(
    generated_text_jmespath="contentBlockDelta.delta.text",
    generated_token_count_jmespath="metadata.usage.outputTokens",  # nosec B106
    input_text_jmespath="messages[].content[].text",
    input_token_count_jmespath="metadata.usage.inputTokens",
)


class FlexBedrockInvokeStream(BedrockInvokeStream):
    """``InvokeModelWithResponseStream`` with a selectable ``serviceTier``.

    Args:
        service_tier: ``"flex"`` to request flex, or ``None`` to request Standard
            (Bedrock has no ``"standard"`` value — Standard = omit the kwarg).
        Other args are forwarded to :class:`llmeter.endpoints.BedrockInvokeStream`
            (notably the ``*_jmespath`` presets that adapt to OpenAI vs Nova
            chunk shapes).
    """

    def __init__(self, *args, service_tier: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._service_tier = service_tier

    @Endpoint.llmeter_invoke
    def invoke(self, payload: dict):
        # Mirror llmeter's BedrockInvokeStream.invoke but add serviceTier. Body
        # serialization stays inside the timed section (parity with llmeter:
        # every transport must serialize for the wire).
        req_body = json.dumps(payload).encode("utf-8")
        kwargs = dict(
            accept="application/json",
            body=req_body,
            contentType="application/json",
            modelId=self.model_id,
        )
        if self._service_tier is not None:
            kwargs["serviceTier"] = self._service_tier
        return self._bedrock_client.invoke_model_with_response_stream(**kwargs)

    def process_raw_response(self, raw_response, start_t: float, response) -> None:
        # Capture served tier from response metadata (available before the body
        # stream is consumed), then delegate to llmeter for timing/token parsing.
        # Narrow except: the response is a dict-like mapping, so only attribute/
        # key/type access can fail here — a genuine bug elsewhere should surface,
        # not be silently turned into served_tier=None.
        served = None
        try:
            served = raw_response.get("serviceTier")
            if not served:
                headers = raw_response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
                served = headers.get(_SERVED_TIER_HEADER)
        except (KeyError, AttributeError, TypeError):
            logger.debug("could not extract served tier from response", exc_info=True)
        super().process_raw_response(raw_response, start_t, response)
        response.served_tier = served


class MantleChatStream(OpenAICompletionStreamEndpoint):
    """Mantle (OpenAI-compatible) streaming chat with a selectable service tier.

    The bearer token is refreshed from the :class:`AuthBroker` inside
    ``prepare_payload``, which llmeter runs *before* starting the latency timer —
    so token handling never inflates a measurement.
    """

    def __init__(
        self,
        model_id: str,
        broker: AuthBroker,
        region: str,
        service_tier: str | None = None,
        endpoint_name: str = "mantle",
    ):
        base_url = broker.mantle_base_url(region)
        # Seed with a token now; refreshed per-call in prepare_payload.
        super().__init__(
            model_id=model_id,
            endpoint_name=endpoint_name,
            api_key=broker.mantle_token(region),
            provider="bedrock-mantle",
            base_url=base_url,
        )
        self._broker = broker
        self._region = region
        self._service_tier = service_tier

    def prepare_payload(self, payload: dict) -> dict:
        # Untimed: refresh credentials and inject the service tier into the body.
        self._client.api_key = self._broker.mantle_token(self._region)
        prepared = super().prepare_payload(payload)
        if self._service_tier is not None:
            extra = dict(prepared.get("extra_body") or {})
            extra["service_tier"] = self._service_tier
            prepared["extra_body"] = extra
        return prepared

    def process_raw_response(self, raw_response, start_t: float, response) -> None:
        # Reimplement llmeter's streaming parse so we can also capture the served
        # tier from chunks (the stream is single-pass; we can't iterate twice).
        served = None
        got_id = False
        for chunk in raw_response:
            now = time.perf_counter()
            tier = getattr(chunk, "service_tier", None)
            if tier:
                served = tier
            if not got_id and getattr(chunk, "id", None):
                response.id = chunk.id
                got_id = True
            if chunk.choices:
                content = chunk.choices[0].delta.content
                if content:
                    if response.response_text is None:
                        response.time_to_first_token = now - start_t
                        response.response_text = content
                    else:
                        response.response_text += content
                    response.time_to_last_token = now - start_t
            if getattr(chunk, "usage", None) is not None:
                response.num_tokens_input = chunk.usage.prompt_tokens
                response.num_tokens_output = chunk.usage.completion_tokens
        response.served_tier = served
