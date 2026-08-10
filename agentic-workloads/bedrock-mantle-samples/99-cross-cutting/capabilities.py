
"""Resolve bedrock-mantle request details per model. Generated from live probes."""

OPENAI_PREFIX_FAMILIES = ("google.gemma-4", "openai.gpt-5", "xai.")

# Models that reject `temperature` at ANY value.
NO_TEMPERATURE = ("xai.grok-4.3", "anthropic.claude-opus-5",
                  "anthropic.claude-sonnet-5", "anthropic.claude-opus-4-8")
# Models that accept `temperature` ONLY at its default 1.0 (Responses API).
# Passing 0.7 to these is a 400 - so on Responses, either send 1.0 or omit it.
TEMPERATURE_DEFAULT_ONLY = ("google.gemma-4", "openai.gpt-5.")
# Models that reject `top_p`.
NO_TOP_P = ("google.gemma-4", "openai.gpt-5.")
# Models that accept flex/priority service tiers.
TIERED = ("openai.gpt-oss", "google.gemma-4", "xai.", "qwen.", "deepseek.",
          "zai.", "minimax.", "moonshotai.", "mistral.", "nvidia.", "writer.")


def api_prefix(model_id):
    if model_id.startswith("anthropic."):
        return "/anthropic/v1"
    if model_id.startswith(OPENAI_PREFIX_FAMILIES):
        return "/openai/v1"
    return "/v1"


def inference_path(model_id, api="auto"):
    prefix = api_prefix(model_id)
    if prefix == "/anthropic/v1":
        return prefix + "/messages"
    if api == "chat":
        return prefix + "/chat/completions"
    return prefix + "/responses"


def sampling(model_id, temperature=None, top_p=None):
    """Drop or clamp parameters this model rejects instead of getting a 400."""
    out = {}
    if temperature is not None and not model_id.startswith(NO_TEMPERATURE):
        if model_id.startswith(TEMPERATURE_DEFAULT_ONLY):
            # Only the default 1.0 is accepted on the Responses API.
            if abs(float(temperature) - 1.0) < 1e-9:
                out["temperature"] = 1.0
            # else: omit entirely rather than 400
        else:
            out["temperature"] = temperature
    if top_p is not None and not model_id.startswith(NO_TOP_P):
        out["top_p"] = top_p
    return out


def service_tier(model_id, tier="default"):
    """Downgrade to 'default' where flex/priority are unsupported."""
    if tier == "default" or model_id.startswith(TIERED):
        return tier
    return "default"


def token_limit_field(model_id, api="auto"):
    """Responses uses max_output_tokens (min 16); Messages/CC use max_tokens."""
    prefix = api_prefix(model_id)
    if prefix == "/anthropic/v1" or api == "chat":
        return "max_tokens"
    return "max_output_tokens"
