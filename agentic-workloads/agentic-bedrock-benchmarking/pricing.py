"""Per-model pricing.

Tries AWS Pricing API first, falls back to a hardcoded table for common Bedrock models.
The fallback ensures the cost column populates even when the user lacks `pricing:GetProducts`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# Pricing API only lives in us-east-1 / ap-south-1 / eu-central-1.
PRICING_REGION = "us-east-1"
SERVICE_CODE = "AmazonBedrock"

# Hardcoded fallback prices. USD per 1k tokens. Sourced from AWS Bedrock public pricing.
# Keyed on the *base* model id (no region prefix). Cross-region inference profiles
# get stripped to their base id before lookup. Patterns are matched as a substring on the id.
# Order matters: most specific patterns first, so 'claude-opus-4' wins over 'claude'.
_FALLBACK_PRICES: list[tuple[str, float, float]] = [
    # Anthropic Claude
    ("anthropic.claude-opus-4-5", 0.005, 0.025),
    ("anthropic.claude-opus-4-1", 0.015, 0.075),
    ("anthropic.claude-opus-4", 0.015, 0.075),
    ("anthropic.claude-sonnet-4-5", 0.003, 0.015),
    ("anthropic.claude-sonnet-4", 0.003, 0.015),
    ("anthropic.claude-haiku-4-5", 0.001, 0.005),
    ("anthropic.claude-3-7-sonnet", 0.003, 0.015),
    ("anthropic.claude-3-5-sonnet", 0.003, 0.015),
    ("anthropic.claude-3-5-haiku", 0.0008, 0.004),
    ("anthropic.claude-3-opus", 0.015, 0.075),
    ("anthropic.claude-3-sonnet", 0.003, 0.015),
    ("anthropic.claude-3-haiku", 0.00025, 0.00125),
    # Amazon Nova
    ("amazon.nova-pro", 0.0008, 0.0032),
    ("amazon.nova-lite", 0.00006, 0.00024),
    ("amazon.nova-micro", 0.000035, 0.00014),
    ("amazon.nova-premier", 0.0025, 0.0125),
    # Meta Llama
    ("meta.llama3-3-70b", 0.00072, 0.00072),
    ("meta.llama3-2-90b", 0.00072, 0.00072),
    ("meta.llama3-2-11b", 0.00016, 0.00016),
    ("meta.llama3-2-3b", 0.00015, 0.00015),
    ("meta.llama3-2-1b", 0.0001, 0.0001),
    ("meta.llama3-1-405b", 0.00532, 0.016),
    ("meta.llama3-1-70b", 0.00072, 0.00072),
    ("meta.llama3-1-8b", 0.00022, 0.00022),
    ("meta.llama3-70b", 0.00265, 0.0035),
    ("meta.llama3-8b", 0.0003, 0.0006),
    # Mistral
    ("mistral.mistral-large-2407", 0.002, 0.006),
    ("mistral.mistral-large", 0.004, 0.012),
    ("mistral.mistral-small", 0.001, 0.003),
    ("mistral.ministral-3-8b", 0.0001, 0.0001),  # newer Ministral 3
    ("mistral.ministral-8b", 0.0001, 0.0001),
    ("mistral.ministral-3b", 0.00004, 0.00004),
    ("mistral.ministral", 0.0001, 0.0001),  # generic fallback for any other ministral variant
    ("mistral.mixtral-8x7b", 0.00045, 0.0007),
    ("mistral.mistral-7b", 0.00015, 0.0002),
    # Cohere
    ("cohere.command-r-plus", 0.003, 0.015),
    ("cohere.command-r", 0.0005, 0.0015),
    ("cohere.command-light", 0.0003, 0.0006),
    ("cohere.command", 0.0015, 0.002),
    # AI21
    ("ai21.jamba-1-5-large", 0.002, 0.008),
    ("ai21.jamba-1-5-mini", 0.0002, 0.0004),
    # DeepSeek
    ("deepseek.r1", 0.00135, 0.0054),
    # Moonshot
    ("moonshot.kimi-k2", 0.0006, 0.0025),
    # Google (via Bedrock partners — pricing is best-effort)
    ("google.gemma", 0.0001, 0.0001),
]


def _fallback_lookup(base_id: str) -> Optional[tuple[float, float]]:
    """Linear scan of the fallback table; returns (in_per_1k, out_per_1k) or None."""
    needle = base_id.lower()
    for pattern, in_p, out_p in _FALLBACK_PRICES:
        if pattern in needle:
            return in_p, out_p
    return None


@dataclass
class ModelPricing:
    model_id: str
    input_per_1k_usd: Optional[float]   # for plain text/chat input
    output_per_1k_usd: Optional[float]
    image_per_1k_usd: Optional[float] = None       # often the same as input for vision-capable models
    cache_read_per_1k_usd: Optional[float] = None
    raw_skus: int = 0                              # how many SKUs we matched

    @property
    def is_known(self) -> bool:
        return self.input_per_1k_usd is not None and self.output_per_1k_usd is not None


def _client() -> "boto3.client":
    # Pricing API is only accessible from a few regions; pin one.
    return boto3.client("pricing", region_name=PRICING_REGION)


def _strip_region_prefix(model_id: str) -> str:
    """Cross-region inference profile IDs look like 'us.anthropic.claude-...';
    Pricing API SKUs are keyed on the base model id."""
    parts = model_id.split(".")
    if len(parts) >= 3 and len(parts[0]) <= 3:
        return ".".join(parts[1:])
    return model_id


def _extract_price_per_unit(price_per_unit: dict) -> Optional[float]:
    """price_per_unit looks like {'USD': '0.003'}. Return the float in USD."""
    if not price_per_unit:
        return None
    usd = price_per_unit.get("USD")
    if usd is None:
        return None
    try:
        return float(usd)
    except (TypeError, ValueError):
        return None


def _classify_sku(attrs: dict) -> Optional[str]:
    """Bucket a SKU into 'input' | 'output' | 'image' | 'cache_read' | None."""
    # The Bedrock service has many attributes. The most reliable signals:
    #  - 'usagetype' (e.g. 'USE1-bedrock-anthropic-claude-3-5-sonnet-input-tokens')
    #  - 'feature' (sometimes 'OnDemandInputTokens' / 'OnDemandOutputTokens')
    #  - 'inferenceType' or 'pricingPlan'
    blob = " ".join(str(v).lower() for v in attrs.values())

    if "cache" in blob and ("read" in blob or "hit" in blob):
        return "cache_read"
    if "image" in blob and "token" not in blob:
        # We only care about per-image input pricing; many models price images per-1k tokens
        # in which case it's already 'input'. Skip explicit per-image SKUs.
        return None
    if "output" in blob:
        return "output"
    if "input" in blob:
        return "input"
    return None


_pricing_cache: dict[str, ModelPricing] = {}


def get_pricing(model_id: str) -> ModelPricing:
    if model_id in _pricing_cache:
        return _pricing_cache[model_id]

    base_id = _strip_region_prefix(model_id)
    result = ModelPricing(model_id=model_id, input_per_1k_usd=None, output_per_1k_usd=None)

    try:
        client = _client()
        # Pricing API uses GetProducts with filters. The most useful filter is 'model'.
        # But model attribute names vary; best to fetch by usagetype regex.
        token = None
        skus_seen = 0
        # We pull a filtered slice keyed on the base model id substring.
        filters = [
            {"Type": "TERM_MATCH", "Field": "model", "Value": base_id},
        ]
        while True:
            kwargs = {"ServiceCode": SERVICE_CODE, "Filters": filters, "MaxResults": 100}
            if token:
                kwargs["NextToken"] = token
            resp = client.get_products(**kwargs)
            for raw in resp.get("PriceList", []):
                doc = json.loads(raw) if isinstance(raw, str) else raw
                attrs = doc.get("product", {}).get("attributes", {}) or {}
                bucket = _classify_sku(attrs)
                if bucket is None:
                    continue

                terms = doc.get("terms", {}).get("OnDemand", {})
                for term in terms.values():
                    for dim in term.get("priceDimensions", {}).values():
                        unit = (dim.get("unit") or "").lower()
                        price = _extract_price_per_unit(dim.get("pricePerUnit", {}))
                        if price is None:
                            continue
                        # Convert to per-1k tokens.
                        if "1k" in unit or "1000" in unit:
                            per_1k = price
                        elif "token" in unit:
                            per_1k = price * 1000
                        else:
                            continue
                        skus_seen += 1
                        if bucket == "input" and result.input_per_1k_usd is None:
                            result.input_per_1k_usd = per_1k
                        elif bucket == "output" and result.output_per_1k_usd is None:
                            result.output_per_1k_usd = per_1k
                        elif bucket == "cache_read" and result.cache_read_per_1k_usd is None:
                            result.cache_read_per_1k_usd = per_1k
            token = resp.get("NextToken")
            if not token:
                break
        result.raw_skus = skus_seen
    except (ClientError, BotoCoreError):
        pass

    # Fallback: hardcoded table for common Bedrock models.
    if not result.is_known:
        fb = _fallback_lookup(base_id)
        if fb is not None:
            result.input_per_1k_usd, result.output_per_1k_usd = fb

    # Only cache successful lookups; let unknowns retry on next access (e.g. after a fallback table update).
    if result.is_known:
        _pricing_cache[model_id] = result
    return result


def estimate_cost_usd(pricing: ModelPricing, input_tokens: int, output_tokens: int) -> Optional[float]:
    if not pricing.is_known:
        return None
    cost_in = (input_tokens / 1000.0) * (pricing.input_per_1k_usd or 0)
    cost_out = (output_tokens / 1000.0) * (pricing.output_per_1k_usd or 0)
    return cost_in + cost_out


def format_per_million(per_1k: Optional[float]) -> str:
    """Convert per-1k pricing to per-million for display."""
    if per_1k is None:
        return "—"
    per_million = per_1k * 1000
    if per_million < 0.01:
        return f"${per_million:.4f}"
    if per_million < 1:
        return f"${per_million:.3f}"
    return f"${per_million:.2f}"


def format_cost(usd: Optional[float]) -> str:
    if usd is None:
        return "—"
    if usd < 0.0001:
        return "<$0.0001"
    if usd < 0.01:
        return f"${usd:.4f}"
    if usd < 1:
        return f"${usd:.3f}"
    return f"${usd:.2f}"


def clear_cache() -> None:
    _pricing_cache.clear()
