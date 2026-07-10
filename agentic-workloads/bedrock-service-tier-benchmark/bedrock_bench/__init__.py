"""bedrock_bench — Benchmark AWS Bedrock service tiers.

Compares Time-To-First-Token (TTFT) and total latency across Bedrock's three
on-demand service tiers — ``default`` (Standard), ``flex``, and ``priority`` —
benchmarking ``flex`` and ``priority`` each against the ``default`` baseline,
across model families and two transports:

* **InvokeModel** — ``bedrock-runtime`` ``InvokeModelWithResponseStream`` (SigV4).
* **Mantle** — the OpenAI-compatible ``bedrock-mantle`` endpoint (bearer token).

Measurement is delegated to AWS Labs' `llmeter <https://github.com/awslabs/llmeter>`_
(its endpoint classes + ``InvocationResponse`` already parse streaming deltas and
record ``time_to_first_token`` / ``time_to_last_token`` robustly). This package
adds the three things llmeter does not provide for this use case:

1. ``serviceTier`` pass-through (stock llmeter has a ``# TODO: serviceTier``),
2. a request-paced scheduler (1 request / model / minute; llmeter's ``Runner`` is
   a fire-as-fast-as-possible load generator), and
3. tier-comparison percentile reporting (flex and priority vs default).
"""

from .config import BenchmarkConfig, PayloadStyle, Tier, Transport

__all__ = ["BenchmarkConfig", "PayloadStyle", "Tier", "Transport"]
__version__ = "0.2.1"
