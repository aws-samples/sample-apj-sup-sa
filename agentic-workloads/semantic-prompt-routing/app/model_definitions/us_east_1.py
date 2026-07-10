MODEL_DEFINITIONS = {
    # ========================================================================
    # Tier 1 - Budget Models ($0.035-0.09 per 1M input tokens)
    # ========================================================================

    "amazon.nova-micro-v1:0": {
        "family": "Nova",
        "tier": 1,
        "context_window": 128_000,
        "capabilities": (),
        "rpm": 1000,
        "tpm": 500_000,
        "input_price_per_1m": 0.035,
        "output_price_per_1m": 0.14
    },
    "google.gemma-3-4b-it": {
        "family": "Gemma",
        "tier": 1,
        "context_window": 8_192,
        "capabilities": ("lightweight",),
        "rpm": 1000,
        "tpm": 500_000,
        "input_price_per_1m": 0.04,
        "output_price_per_1m": 0.08
    },
    "amazon.nova-lite-v1:0": {
        "family": "Nova",
        "tier": 1,
        "context_window": 300_000,
        "capabilities": ("multimodal",),
        "rpm": 1000,
        "tpm": 800_000,
        "input_price_per_1m": 0.06,
        "output_price_per_1m": 0.24
    },
    "zai.glm-4.7-flash": {
        "family": "GLM",
        "tier": 1,
        "context_window": 203_000,
        "capabilities": ("cjk",),
        "rpm": 1000,
        "tpm": 500_000,
        "input_price_per_1m": 0.07,
        "output_price_per_1m": 0.40
    },
    "google.gemma-3-12b-it": {
        "family": "Gemma",
        "tier": 1,
        "context_window": 128_000,
        "capabilities": (),
        "rpm": 1000,
        "tpm": 600_000,
        "input_price_per_1m": 0.09,
        "output_price_per_1m": 0.29
    },

    # ========================================================================
    # Tier 2 - Standard Models ($0.17-0.23 per 1M input tokens)
    # ========================================================================

    "us.meta.llama4-scout-17b-instruct-v1:0": {
        "family": "Llama4",
        "tier": 2,
        "context_window": 10_000_000,
        "capabilities": ("long_context",),
        "rpm": 800,
        "tpm": 1_000_000,
        "input_price_per_1m": 0.17,
        "output_price_per_1m": 0.66
    },
    "qwen.qwen3-32b-v1:0": {
        "family": "Qwen",
        "tier": 2,
        "context_window": 32_000,
        "capabilities": ("code", "cjk", "tools"),
        "rpm": 800,
        "tpm": 800_000,
        "input_price_per_1m": 0.15,
        "output_price_per_1m": 0.60
    },
    "us.meta.llama4-maverick-17b-instruct-v1:0": {
        "family": "Llama4",
        "tier": 2,
        "context_window": 1_000_000,
        "capabilities": ("long_context",),
        "rpm": 800,
        "tpm": 900_000,
        "input_price_per_1m": 0.24,
        "output_price_per_1m": 0.97
    },
    "google.gemma-3-27b-it": {
        "family": "Gemma",
        "tier": 2,
        "context_window": 128_000,
        "capabilities": (),
        "rpm": 800,
        "tpm": 700_000,
        "input_price_per_1m": 0.23,
        "output_price_per_1m": 0.38
    },

    # ========================================================================
    # Tier 3 - Advanced Models ($0.60-0.80 per 1M input tokens)
    # ========================================================================

    "moonshotai.kimi-k2.5": {
        "family": "Kimi",
        "tier": 3,
        "context_window": 256_000,
        "capabilities": ("vision", "tools"),
        "rpm": 500,
        "tpm": 600_000,
        "input_price_per_1m": 0.60,
        "output_price_per_1m": 3.00
    },
    "deepseek.v3.2": {
        "family": "DeepSeek",
        "tier": 3,
        "context_window": 164_000,
        "capabilities": ("code", "math"),
        "rpm": 500,
        "tpm": 500_000,
        "input_price_per_1m": 0.62,
        "output_price_per_1m": 1.85
    },
    "zai.glm-4.7": {
        "family": "GLM",
        "tier": 3,
        "context_window": 203_000,
        "capabilities": ("cjk",),
        "rpm": 500,
        "tpm": 500_000,
        "input_price_per_1m": 0.60,
        "output_price_per_1m": 2.20
    },
    "amazon.nova-pro-v1:0": {
        "family": "Nova",
        "tier": 3,
        "context_window": 300_000,
        "capabilities": ("agents", "rag", "tools"),
        "rpm": 500,
        "tpm": 800_000,
        "input_price_per_1m": 0.80,
        "output_price_per_1m": 3.20
    },
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": {
        "family": "Claude",
        "tier": 3,
        "context_window": 200_000,
        "capabilities": ("tool_use",),
        "rpm": 500,
        "tpm": 600_000,
        "input_price_per_1m": 1.00,
        "output_price_per_1m": 5.00
    },

    # ========================================================================
    # Tier 4 - Expert Models ($3.00+ per 1M input tokens)
    # ========================================================================

    "us.anthropic.claude-sonnet-4-6": {
        "family": "Claude",
        "tier": 4,
        "context_window": 1_000_000,
        "capabilities": ("expert", "tools"),
        "rpm": 200,
        "tpm": 400_000,
        "input_price_per_1m": 3.00,
        "output_price_per_1m": 15.00
    },
    "us.anthropic.claude-opus-4-7": {
        "family": "Claude",
        "tier": 4,
        "context_window": 1_000_000,
        "capabilities": ("expert", "tools"),
        "rpm": 200,
        "tpm": 400_000,
        "input_price_per_1m": 5.00,
        "output_price_per_1m": 25.00
    },
}