"""Model configuration definitions and dynamic builder.

This module contains:
1. Model configuration data structure
2. Dynamic model config builder (loads from region-specific files)
3. Global MODEL_CONFIGS that gets populated at router initialization
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Dict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model Configuration Data Structure
# ---------------------------------------------------------------------------

@dataclass
class ModelConfig:
    """Enhanced model config for LiteLLM + Bedrock."""

    model_id: str  # Bedrock model ID
    litellm_name: str  # LiteLLM-compatible name
    family: str
    tier: int
    input_price: float  # USD per 1M tokens
    output_price: float  # USD per 1M tokens
    context_window: int
    capabilities: tuple[str, ...] = ()
    rpm: int = 1000  # Requests per minute limit
    tpm: int = 1_000_000  # Tokens per minute limit


# ---------------------------------------------------------------------------
# Global Model Configurations (populated dynamically)
# ---------------------------------------------------------------------------

# These will be built by build_model_configs() based on region
MODEL_CONFIGS: list[ModelConfig] = []
MODEL_BY_ID: Dict[str, ModelConfig] = {}
MODEL_BY_LITELLM_NAME: Dict[str, ModelConfig] = {}


def load_region_definitions(region: str) -> Dict:
    """Load model definitions from region-specific file.

    Args:
        region: AWS region (e.g., 'us-east-1', 'us-west-2')

    Returns:
        Dictionary of model definitions for that region

    Raises:
        ImportError: If region definitions file doesn't exist
    """
    # Convert region format: us-east-1 -> us_east_1
    region_module_name = region.replace('-', '_')

    try:
        # Import the region-specific module
        module = importlib.import_module(f'app.model_definitions.{region_module_name}')

        if not hasattr(module, 'MODEL_DEFINITIONS'):
            raise ImportError(f"Region {region} is not supported")

        definitions = module.MODEL_DEFINITIONS
        logger.info(f"Loaded {len(definitions)} model definitions for region {region}")

        return definitions

    except ImportError as e:
        logger.error(
            f"No model definitions found for region '{region}'. "
            f"Looking for: app/model_definitions/{region_module_name}.py"
        )
        raise ImportError(
            f"Region '{region}' not supported. "
            f"Please create app/model_definitions/{region_module_name}.py with MODEL_DEFINITIONS"
        ) from e


def build_model_configs(region: str) -> list[ModelConfig]:
    """Build MODEL_CONFIGS from region-specific definitions file.

    This function:
    1. Loads model definitions from app/model_definitions/{region}.py
    2. Creates ModelConfig objects for each model
    3. Populates global MODEL_CONFIGS, MODEL_BY_ID, MODEL_BY_LITELLM_NAME

    Args:
        region: AWS region (e.g., 'us-east-1', 'us-west-2')

    Returns:
        List of ModelConfig objects for that region

    Raises:
        ImportError: If region definitions file doesn't exist

    Example:
        >>> configs = build_model_configs('us-east-1')
        >>> len(configs)
        16  # Number of models defined for us-east-1
    """
    global MODEL_CONFIGS, MODEL_BY_ID, MODEL_BY_LITELLM_NAME

    # Clear existing configs
    MODEL_CONFIGS.clear()
    MODEL_BY_ID.clear()
    MODEL_BY_LITELLM_NAME.clear()

    # Load region-specific definitions
    model_definitions = load_region_definitions(region)

    # Build ModelConfig objects
    for model_id, definition in model_definitions.items():
        config = ModelConfig(
            model_id=model_id,
            litellm_name=f"bedrock/{model_id}",
            family=definition["family"],
            tier=definition["tier"],
            input_price=definition["input_price_per_1m"],
            output_price=definition["output_price_per_1m"],
            context_window=definition["context_window"],
            capabilities=definition.get("capabilities", ()),
            rpm=definition.get("rpm", 1000),
            tpm=definition.get("tpm", 1_000_000),
        )
        MODEL_CONFIGS.append(config)

    # Build lookup dictionaries
    MODEL_BY_ID = {m.model_id: m for m in MODEL_CONFIGS}
    MODEL_BY_LITELLM_NAME = {m.litellm_name: m for m in MODEL_CONFIGS}

    logger.info(f"Built MODEL_CONFIGS: {len(MODEL_CONFIGS)} models for {region}")

    return MODEL_CONFIGS


def get_model_by_id(model_id: str) -> ModelConfig | None:
    """Get model config by Bedrock model ID."""
    return MODEL_BY_ID.get(model_id)


def get_model_by_litellm_name(litellm_name: str) -> ModelConfig | None:
    """Get model config by LiteLLM model name."""
    return MODEL_BY_LITELLM_NAME.get(litellm_name)


def get_available_families() -> set[str]:
    """Get set of available model families."""
    return {m.family for m in MODEL_CONFIGS}


def get_available_tiers() -> set[int]:
    """Get set of available tiers."""
    return {m.tier for m in MODEL_CONFIGS}


def get_models_by_family(family: str) -> list[ModelConfig]:
    """Get all models in a specific family."""
    return [m for m in MODEL_CONFIGS if m.family == family]


def get_models_by_tier(tier: int) -> list[ModelConfig]:
    """Get all models in a specific tier."""
    return [m for m in MODEL_CONFIGS if m.tier == tier]


def get_supported_regions() -> list[str]:
    """Get list of regions with model definitions available.

    Returns:
        List of region codes (e.g., ['us-east-1', 'us-west-2'])
    """
    import os
    import glob

    definitions_dir = os.path.join(os.path.dirname(__file__), 'model_definitions')
    region_files = glob.glob(os.path.join(definitions_dir, '*.py'))

    regions = []
    for file_path in region_files:
        filename = os.path.basename(file_path)
        if filename == '__init__.py':
            continue
        # Convert us_east_1.py -> us-east-1
        region = filename.replace('.py', '').replace('_', '-')
        regions.append(region)

    return sorted(regions)


def get_model_configs() -> list[ModelConfig]:
    """Get the current MODEL_CONFIGS list.

    This function allows other modules to access the dynamically populated
    MODEL_CONFIGS without import reference issues.
    """
    return MODEL_CONFIGS
