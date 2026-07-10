"""Semantic Router: Intelligent Classification + Cost-Optimized Model Selection.

Combines semantic query analysis (complexity, domain, language) with cost-aware
model selection for AWS Bedrock.

Architecture:
1. Query Classification → ComplexitySignals
2. Semantic Model Selection (complexity, task type, language aware)
3. Direct Bedrock API Calls
4. Cost Tracking & Analytics

Region-Aware:
- Loads region-specific model definitions with pricing
- Filters models to only those available in the region
- Falls back gracefully when models are unavailable
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional, Dict, Set

import boto3

from .routing.classifier import ComplexitySignals, classify_query
from .model_config import (
    ModelConfig,
    MODEL_BY_ID,
    build_model_configs,
    get_available_families,
    get_available_tiers,
    get_models_by_tier,
    get_model_configs,
)

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Semantic Model Selection
# ---------------------------------------------------------------------------

class SemanticSelector:
    """Select models based on query semantics (complexity, domain, language)."""

    def __init__(self, enabled_families: set[str] | None = None):
        self._enabled_families_filter = enabled_families

    @property
    def enabled_families(self):
        """Get enabled families (defaults to all available families if not specified)."""
        if self._enabled_families_filter is None:
            return {m.family for m in get_model_configs()}
        return self._enabled_families_filter

    @property
    def _available_models(self):
        """Get available models dynamically (in case MODEL_CONFIGS was populated after init)."""
        configs = get_model_configs()
        if self._enabled_families_filter is None:
            return configs
        return [m for m in configs if m.family in self._enabled_families_filter]

    def select_model(self, signals: ComplexitySignals) -> tuple[str, str]:
        """Select the best model based on classification signals.

        Returns:
            (model_id, reason)
        """
        # Override 1: Long context needs
        if signals.token_count > 100_000:
            model = self._find_model_with_capability("long_context")
            if model:
                return (
                    model.model_id,
                    f"Long context ({signals.token_count} tokens) → {model.family}"
                )

        # Override 2: Code tasks
        if signals.is_code:
            if signals.complexity_score > 0.6:
                # High complexity code → DeepSeek V3
                model = self._find_model("DeepSeek", 3)
                if model:
                    return (
                        model.model_id,
                        f"Complex code (score={signals.complexity_score:.2f}) → DeepSeek V3"
                    )
            else:
                # Medium code → Qwen 32B
                model = self._find_model("Qwen", 2)
                if model:
                    return (
                        model.model_id,
                        f"Code task (score={signals.complexity_score:.2f}) → Qwen 32B"
                    )

        # Override 3: CJK languages
        if signals.is_cjk:
            # Prefer models with CJK capability
            model = self._find_model_with_capability("cjk")
            if model:
                return (
                    model.model_id,
                    f"CJK language ({signals.language}) → {model.family}"
                )

        # Override 4: Structured output
        if signals.has_structured_output:
            model = self._find_model("Claude", 3)  # Claude Haiku excels at this
            if model:
                return (
                    model.model_id,
                    "Structured output → Claude Haiku"
                )

        # Default: Route by complexity score
        target_tier = self._complexity_to_tier(signals.complexity_score)
        model = self._find_cheapest_in_tier(target_tier)

        if model:
            return (
                model.model_id,
                f"Complexity {signals.complexity_score:.2f} → Tier {target_tier} ({model.family})"
            )

        # Fallback to cheapest available
        available = self._available_models
        if not available:
            raise RuntimeError(
                "No models available for routing. Ensure build_model_configs() has been called "
                "and models are enabled for the selected families."
            )

        fallback = min(available, key=lambda m: m.input_price)
        return (
            fallback.model_id,
            f"Fallback to cheapest available ({fallback.family})"
        )

    def _complexity_to_tier(self, score: float) -> int:
        """Map complexity score to tier."""
        if score < 0.3:
            return 1
        elif score < 0.5:
            return 2
        elif score < 0.7:
            return 3
        else:
            return 4

    def _find_model(self, family: str, tier: int) -> Optional[ModelConfig]:
        """Find specific model by family and tier."""
        for model in self._available_models:
            if model.family == family and model.tier == tier:
                return model
        return None

    def _find_model_with_capability(self, capability: str) -> Optional[ModelConfig]:
        """Find cheapest model with given capability."""
        candidates = [
            m for m in self._available_models
            if capability in m.capabilities
        ]
        if candidates:
            return min(candidates, key=lambda m: m.input_price)
        return None

    def _find_cheapest_in_tier(self, tier: int) -> Optional[ModelConfig]:
        """Find cheapest model in given tier."""
        candidates = [m for m in self._available_models if m.tier == tier]
        if candidates:
            return min(candidates, key=lambda m: m.input_price)
        # If tier not available, try tier below
        if tier > 1:
            return self._find_cheapest_in_tier(tier - 1)
        return None


# ---------------------------------------------------------------------------
# Hybrid Router (Main Interface)
# ---------------------------------------------------------------------------

@dataclass
class RoutingResult:
    """Result from semantic routing."""

    response: str
    model_used: str  # Bedrock model ID
    family: str
    tier: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_s: float
    routing_explanation: str
    classification_signals: ComplexitySignals
    error: Optional[str] = None


class SemanticRouter:
    """Semantic router with intelligent classification and cost-optimized model selection.

    Features:
    - Intelligent query analysis (complexity, domain, language)
    - Cost-aware model selection
    - Direct AWS Bedrock API calls
    - Region-specific model configurations

    Args:
        enabled_families: Restrict to these model families
        region: AWS region (auto-detected if None)
    """

    def __init__(
        self,
        enabled_families: set[str] | None = None,
        region: str | None = None,
    ):
        """Initialize the semantic router with region-aware model selection.

        Args:
            enabled_families: Optional family filter (e.g., {"Nova", "Claude"})
            region: AWS region (auto-detected if None)
        """
        # Detect region from boto3 session or environment
        if region is None:
            session = boto3.Session()
            region = session.region_name or os.environ.get('AWS_REGION', 'us-east-1')

        self.region = region
        logger.info(f"Initializing SemanticRouter in region: {region}")

        # Build MODEL_CONFIGS from region-specific definitions file
        try:
            build_model_configs(region)
        except ImportError as e:
            from model_config import get_supported_regions
            supported = get_supported_regions()
            raise RuntimeError(
                f"No model definitions found for region '{region}'.\n"
                f"Supported regions: {', '.join(supported)}\n"
                f"To add support for {region}, create: app/model_definitions/{region.replace('-', '_')}.py"
            ) from e

        # Validate we have models to use
        if not get_model_configs():
            raise RuntimeError(
                f"No models defined for region {region}. "
                f"Check app/model_definitions/{region.replace('-', '_')}.py"
            )

        # Set enabled families (with user override)
        self.enabled_families = enabled_families or get_available_families()

        # Warn if requested families are unavailable
        available_families = get_available_families()
        if enabled_families:
            unavailable = enabled_families - available_families
            if unavailable:
                logger.warning(
                    f"⚠️  Requested families unavailable in {region}: {', '.join(unavailable)}"
                )
                logger.info(f"💡 Available families: {', '.join(sorted(available_families))}")

        # Initialize semantic selector
        self.selector = SemanticSelector(self.enabled_families)

        # Direct Bedrock client
        self.bedrock_client = boto3.client("bedrock-runtime", region_name=region)

        # Tracking
        self._request_count = 0
        self._total_cost = 0.0
        self._total_latency = 0.0

        logger.info(
            f"Router ready: {len(get_model_configs())} models, "
            f"{len(self.enabled_families)} families, "
            f"{len(get_available_tiers())} tiers"
        )

    async def route_and_respond(
        self,
        query: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> RoutingResult:
        """Main routing entrypoint: classify → select → route → respond.

        Args:
            query: User query
            conversation_history: Prior conversation turns

        Returns:
            RoutingResult with response, model info, costs, etc.
        """
        t0 = time.perf_counter()

        # Step 1: Classify query semantics
        signals = await classify_query(query, conversation_history) # change this to classify_query_ollama(query, conversation_history) to use Ollama as the classifier instead
        logger.info(
            "Classification: complexity=%.2f, task=%s, lang=%s",
            signals.complexity_score, signals.task_type, signals.language
        )

        # Step 2: Select model based on semantics
        model_id, routing_reason = self.selector.select_model(signals)
        logger.info("Selected model: %s (%s)", model_id, routing_reason)

        # Step 3: Call Bedrock
        result = await self._call_bedrock(
            model_id, query, conversation_history or []
        )

        latency = time.perf_counter() - t0

        # Extract model info
        model_cfg = MODEL_BY_ID.get(result["model_id"])
        if not model_cfg:
            # Fallback parsing
            configs = get_model_configs()
            model_cfg = next(
                (m for m in configs if m.model_id == result["model_id"]),
                configs[0]  # Last resort
            )

        # Calculate cost
        cost = (
            result["input_tokens"] * model_cfg.input_price / 1_000_000 +
            result["output_tokens"] * model_cfg.output_price / 1_000_000
        )

        # Update tracking
        self._request_count += 1
        self._total_cost += cost
        self._total_latency += latency

        return RoutingResult(
            response=result["response"],
            model_used=result["model_id"],
            family=model_cfg.family,
            tier=model_cfg.tier,
            input_tokens=result["input_tokens"],
            output_tokens=result["output_tokens"],
            cost_usd=cost,
            latency_s=latency,
            routing_explanation=routing_reason + f" | {signals.reasoning}",
            classification_signals=signals,
            # fallback_used=result.get("fallback_used", False),
            error=result.get("error"),
        )

    async def _call_bedrock(
        self,
        model_id: str,
        query: str,
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Call AWS Bedrock API directly."""
        messages = self._build_bedrock_messages(query, history)

        try:
            response = await asyncio.to_thread(
                self.bedrock_client.converse,
                modelId=model_id,
                messages=messages,
                inferenceConfig={"maxTokens": 4096, "temperature": 0.7},
            )

            text = response["output"]["message"]["content"][0]["text"]
            usage = response.get("usage", {})

            return {
                "response": text,
                "model_id": model_id,
                "input_tokens": usage.get("inputTokens", 0),
                "output_tokens": usage.get("outputTokens", 0),
            }

        except Exception as e:
            logger.error("Bedrock call failed for %s: %s", model_id, e, exc_info=True)
            return {
                "response": f"Error: {str(e)}",
                "model_id": model_id,
                "input_tokens": 0,
                "output_tokens": 0,
                "error": str(e),
            }

    def _build_bedrock_messages(
        self,
        query: str,
        history: list[dict[str, Any]],
    ) -> list[dict]:
        """Build message list for Bedrock converse API."""
        messages = []
        for turn in history:
            messages.append({
                "role": turn.get("role", "user"),
                "content": [{"text": turn.get("content", turn.get("text", ""))}],
            })
        messages.append({"role": "user", "content": [{"text": query}]})
        return messages

    def get_stats(self) -> dict[str, Any]:
        """Get routing statistics."""
        return {
            "total_requests": self._request_count,
            "total_cost_usd": round(self._total_cost, 6),
            "total_latency_s": round(self._total_latency, 3),
            "avg_cost_per_request": (
                round(self._total_cost / max(self._request_count, 1), 6)
            ),
            "avg_latency_per_request": (
                round(self._total_latency / max(self._request_count, 1), 3)
            ),
        }
