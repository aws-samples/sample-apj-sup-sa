"""bedrock_bench — benchmark Amazon Bedrock service tiers across endpoints, APIs and workloads.

Compares latency of Bedrock's service tiers (``default``/Standard, ``flex``,
``priority``, and ``reserved`` where reserved capacity exists) for every
supported combination of endpoint (``bedrock-runtime``, ``bedrock-mantle``),
API (Converse, InvokeModel, Chat Completions, Responses, Messages), inference
scope (in-Region, geo, global), prompt size and prompt-cache condition.

Measurement follows NVIDIA AIPerf / Artificial Analysis definitions (TTFT,
time to first answer token, end-to-end latency, inter-token latency). See
``docs/DESIGN.md`` for the methodology.
"""

from .config import (
    Api,
    BenchmarkConfig,
    CacheMode,
    Endpoint,
    PromptSize,
    Scope,
    Tier,
)

__all__ = ["Api", "BenchmarkConfig", "CacheMode", "Endpoint", "PromptSize", "Scope", "Tier"]
__version__ = "1.0.0"
