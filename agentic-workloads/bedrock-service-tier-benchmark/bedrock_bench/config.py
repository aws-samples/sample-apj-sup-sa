"""Core enums and the top-level benchmark configuration object.

These types are deliberately free of any AWS/llmeter imports so they can be
imported cheaply (e.g. by the CLI for ``--help``) and unit-tested in isolation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

#: Syntactic validation for AWS region strings across partitions — standard
#: (``us-east-1``, ``ap-southeast-2``), GovCloud (``us-gov-west-1``), and China
#: (``cn-north-1``). The region is interpolated into the Bedrock endpoint host,
#: so it is validated before use to avoid building an unexpected URL.
_AWS_REGION_RE = re.compile(r"^[a-z]{2}-(gov-)?[a-z]+-\d{1,2}$")


def validate_region(region: str) -> str:
    """Return ``region`` if it is a syntactically valid AWS region, else raise.

    Shared by both the benchmark and discovery entry points so the same value is
    validated consistently before it is used to build a Bedrock endpoint host.

    Args:
        region: An AWS region string (e.g. ``us-east-1``).

    Returns:
        The validated region, unchanged.

    Raises:
        ValueError: If ``region`` is not a syntactically valid AWS region.
    """
    if not _AWS_REGION_RE.match(region):
        raise ValueError(
            f"invalid AWS region {region!r} (expected e.g. 'us-east-1'); "
            f"this value is used to build the Bedrock endpoint host."
        )
    return region


class Tier(str, Enum):
    """Bedrock service tier under test.

    Bedrock has no ``"standard"`` wire value: Standard tier is selected by
    *omitting* the service tier or sending ``"default"``. We model that as
    :attr:`DEFAULT` and the endpoint adapters translate it to "send nothing".

    :attr:`FLEX` (cheaper, latency-tolerant) and :attr:`PRIORITY` (premium,
    preferential processing) are the two non-default tiers; both are always
    compared *against* default.
    """

    DEFAULT = "default"
    FLEX = "flex"
    PRIORITY = "priority"

    @property
    def is_default(self) -> bool:
        return self is Tier.DEFAULT


class Transport(str, Enum):
    """How the request reaches the model."""

    #: bedrock-runtime InvokeModelWithResponseStream (SigV4 / boto3).
    INVOKE = "invoke"
    #: bedrock-mantle OpenAI-compatible Chat Completions (bearer token).
    MANTLE = "mantle"


class PayloadStyle(str, Enum):
    """Request/response schema a model expects on the InvokeModel transport.

    Discovered empirically against live Bedrock:

    * :attr:`OPENAI` — open-weight models (GLM, DeepSeek, Kimi, Qwen, GPT-OSS)
      speak an OpenAI ChatCompletions-like body and stream
      ``choices[0].delta.content`` chunks.
    * :attr:`NOVA` — Amazon Nova models use the Bedrock Converse-native body
      (``messages[].content[].text`` + ``inferenceConfig``) and stream
      ``contentBlockDelta`` events.

    The Mantle transport is always OpenAI-shaped (it *is* an OpenAI endpoint),
    so this only varies the InvokeModel adapter.
    """

    OPENAI = "openai"
    NOVA = "nova"


# A single fixed prompt keeps input-token count constant across every cell, so
# TTFT/latency differences reflect the tier and not prompt variance. It asks for
# a bounded, deterministic-ish output so reasoning models still emit visible
# tokens within max_tokens (see GPT-OSS reasoning behaviour in design notes).
DEFAULT_PROMPT = (
    "Write two concise sentences explaining what latency means for an online service, then stop."
)


@dataclass(frozen=True)
class BenchmarkConfig:
    """Top-level knobs for a benchmark run.

    Attributes:
        profile: AWS named profile providing credentials. ``None`` (the default)
            lets boto3 resolve credentials via the standard chain
            (``AWS_PROFILE`` env var, then default profile, then instance/role
            credentials) — so no personal profile name is baked into the tool.
        regions: Region preference order for InvokeModel/Mantle invocation. The
            registry picks the first region in which a given model is available.
            Per requirements: in-region us-east-1 / us-west-2 (these open-weight
            flex models expose no global/geo inference profile).
        n_requests: Samples per cell used for percentile statistics (>= 30).
        interval_seconds: Spacing between requests **within one (transport, model)
            domain** — 60s satisfies the "1 request per model per minute" rule and
            keeps us clear of RPM/TPM throttling. Tiers are interleaved inside the
            domain so a model's default+flex samples share the cadence.
        max_tokens: Output cap. Small enough to stay cheap, large enough that
            reasoning models emit visible tokens (so TTFT is real, not null).
        prompt: The fixed user prompt sent to every model.
        timeout_seconds: Per-request wall-clock ceiling.
        output_dir: Where raw JSONL + summaries are written.
        redact: When True, omit account-identifying metadata (account id, profile)
            from generated reports so they are safe to share externally.
    """

    profile: str | None = None
    regions: tuple[str, ...] = ("us-west-2", "us-east-1")
    n_requests: int = 30
    interval_seconds: float = 60.0
    max_tokens: int = 200
    prompt: str = DEFAULT_PROMPT
    timeout_seconds: float = 120.0
    output_dir: str = "results"
    redact: bool = False

    # Optional filters (None = all). Values match ModelSpec.family / Transport / Tier.
    families: tuple[str, ...] | None = None
    transports: tuple[Transport, ...] = (Transport.INVOKE, Transport.MANTLE)
    tiers: tuple[Tier, ...] = (Tier.DEFAULT, Tier.FLEX, Tier.PRIORITY)

    # Internal: metadata stamped into outputs (set by the orchestrator).
    run_id: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        """Validate the configuration.

        Raises:
            ValueError: If ``n_requests``/``max_tokens`` are not positive,
                ``interval_seconds`` is negative, no regions are given, or any
                region is not a syntactically valid AWS region.
        """
        if self.n_requests < 1:
            raise ValueError("n_requests must be >= 1")
        if self.interval_seconds < 0:
            raise ValueError("interval_seconds must be >= 0")
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be >= 1")
        if not self.regions:
            raise ValueError("at least one region is required")
        for region in self.regions:
            validate_region(region)
