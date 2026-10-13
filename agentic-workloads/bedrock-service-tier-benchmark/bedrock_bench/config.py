"""Benchmark dimensions (enums) and the top-level run configuration.

These types are free of AWS imports so they can be imported cheaply (e.g. by the
CLI for ``--help``) and unit-tested in isolation. See ``docs/DESIGN.md`` for why
each dimension exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

#: Syntactic validation for AWS region strings across partitions — standard
#: (``us-east-1``), GovCloud (``us-gov-west-1``) and China (``cn-north-1``). The
#: region is interpolated into endpoint hostnames, so it is validated before use.
_AWS_REGION_RE = re.compile(r"^[a-z]{2}-(gov-)?[a-z]+-\d{1,2}$")


def validate_region(region: str) -> str:
    """Return ``region`` if it is a syntactically valid AWS region, else raise.

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
    """Bedrock service tier (the treatment under test).

    ``default`` is the Standard tier. ``reserved`` needs a capacity reservation
    arranged with AWS, so it is only benchmarked when explicitly requested.
    """

    DEFAULT = "default"
    FLEX = "flex"
    PRIORITY = "priority"
    RESERVED = "reserved"

    @property
    def is_default(self) -> bool:
        return self is Tier.DEFAULT


class Endpoint(str, Enum):
    """The Bedrock inference endpoint (URL surface) a request is sent to."""

    #: ``bedrock-runtime.{region}.amazonaws.com`` — AWS's recommended endpoint.
    RUNTIME = "runtime"
    #: ``bedrock-mantle.{region}.api.aws`` — OpenAI/Anthropic-compatible surface.
    MANTLE = "mantle"


class Api(str, Enum):
    """The inference API used for a request."""

    CONVERSE_STREAM = "converse_stream"
    CONVERSE = "converse"
    INVOKE_STREAM = "invoke_stream"
    INVOKE = "invoke"
    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"
    MESSAGES = "messages"

    @property
    def streaming(self) -> bool:
        """Whether this API yields a token stream (and therefore a TTFT)."""
        return self not in (Api.CONVERSE, Api.INVOKE)


#: Which APIs each endpoint serves (AWS "Endpoints supported by Amazon Bedrock").
ENDPOINT_APIS: dict[Endpoint, tuple[Api, ...]] = {
    Endpoint.RUNTIME: (
        Api.CONVERSE_STREAM,
        Api.CONVERSE,
        Api.INVOKE_STREAM,
        Api.INVOKE,
        Api.CHAT_COMPLETIONS,
        Api.RESPONSES,
        Api.MESSAGES,
    ),
    Endpoint.MANTLE: (Api.CHAT_COMPLETIONS, Api.RESPONSES, Api.MESSAGES),
}


class Scope(str, Enum):
    """Inference scope: in-Region model id, geographic or global inference profile."""

    IN_REGION = "in_region"
    GEO = "geo"
    GLOBAL = "global"


class PromptSize(str, Enum):
    """Input workload size (aligned with Artificial Analysis 1k / 10k / 100k)."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"

    @property
    def input_tokens(self) -> int:
        """Target input tokens (approximate; actual counts are reported per request).

        ``small`` is 1.5k rather than 1k so its prefix clears the common 1,024-token
        minimum for a prompt-cache checkpoint, letting warm-cache cells run at every size.
        """
        return {"small": 1_500, "medium": 10_000, "large": 100_000}[self.value]

    @property
    def max_output_tokens(self) -> int:
        """Default output cap for this workload."""
        return {"small": 512, "medium": 512, "large": 1_024}[self.value]


class CacheMode(str, Enum):
    """Prompt-cache condition under test."""

    #: Unique nonce + unique document per request; any cache read is flagged.
    COLD = "cold"
    #: Shared document prefix, relying on Bedrock's implicit (automatic) caching.
    WARM_IMPLICIT = "warm_implicit"
    #: Shared document prefix with the API's native explicit cache checkpoint.
    WARM_EXPLICIT = "warm_explicit"

    @property
    def is_warm(self) -> bool:
        return self is not CacheMode.COLD


def _parse_enum_list(enum_cls, values: tuple) -> tuple:
    return tuple(v if isinstance(v, enum_cls) else enum_cls(v) for v in values)


@dataclass(frozen=True)
class BenchmarkConfig:
    """Top-level knobs for a benchmark run.

    Every dimension field is a filter: the cell expander keeps only combinations
    that are listed here **and** that the model registry says are supported.

    Attributes:
        profile: AWS named profile. ``None`` uses the standard boto3 chain.
        regions: Source regions to send requests from (in preference order).
        n_requests: Measured samples per cell (>= 30 recommended).
        interval_seconds: Spacing between request starts within one pacing domain.
        timeout_seconds: Per-request ceiling for non-flex tiers.
        flex_timeout_seconds: Per-request ceiling for the flex tier (it queues).
        warmup_requests: Requests per cell sent first and discarded.
        seed: RNG seed for prompt generation and tier-order shuffling.
        output_dir: Where raw JSONL + reports are written.
        redact: Omit account-identifying metadata from reports.
    """

    profile: str | None = None
    regions: tuple[str, ...] = ("us-east-1",)
    n_requests: int = 30
    interval_seconds: float = 60.0
    timeout_seconds: float = 180.0
    flex_timeout_seconds: float = 600.0
    warmup_requests: int = 1
    seed: int = 20261008
    output_dir: str = "results"
    redact: bool = False

    families: tuple[str, ...] | None = None
    endpoints: tuple[Endpoint, ...] = (Endpoint.RUNTIME, Endpoint.MANTLE)
    apis: tuple[Api, ...] = (
        Api.CONVERSE_STREAM,
        Api.INVOKE_STREAM,
        Api.CHAT_COMPLETIONS,
        Api.RESPONSES,
        Api.MESSAGES,
    )
    scopes: tuple[Scope, ...] = (Scope.IN_REGION, Scope.GEO, Scope.GLOBAL)
    prompt_sizes: tuple[PromptSize, ...] = (PromptSize.SMALL,)
    cache_modes: tuple[CacheMode, ...] = (CacheMode.COLD,)
    tiers: tuple[Tier, ...] = (Tier.DEFAULT, Tier.FLEX, Tier.PRIORITY)
    #: Override per-size output cap (None = PromptSize.max_output_tokens).
    max_output_tokens: int | None = None

    run_id: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        """Validate and normalise the configuration.

        Raises:
            ValueError: On non-positive counts, negative intervals, empty or
                invalid regions, or empty dimension filters.
        """
        if self.n_requests < 1:
            raise ValueError("n_requests must be >= 1")
        if self.warmup_requests < 0:
            raise ValueError("warmup_requests must be >= 0")
        if self.interval_seconds < 0:
            raise ValueError("interval_seconds must be >= 0")
        if self.timeout_seconds <= 0 or self.flex_timeout_seconds <= 0:
            raise ValueError("timeouts must be > 0")
        if self.max_output_tokens is not None and self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be >= 1")
        if not self.regions:
            raise ValueError("at least one region is required")
        for region in self.regions:
            validate_region(region)
        # Accept plain strings for convenience (CLI, tests, the web worker).
        for name, cls in (
            ("endpoints", Endpoint),
            ("apis", Api),
            ("scopes", Scope),
            ("prompt_sizes", PromptSize),
            ("cache_modes", CacheMode),
            ("tiers", Tier),
        ):
            values = _parse_enum_list(cls, tuple(getattr(self, name)))
            if not values:
                raise ValueError(f"{name} must not be empty")
            object.__setattr__(self, name, values)

    def timeout_for(self, tier: Tier) -> float:
        """Per-request timeout for ``tier``."""
        return self.flex_timeout_seconds if tier is Tier.FLEX else self.timeout_seconds

    def output_cap(self, size: PromptSize) -> int:
        """Output-token cap for ``size``."""
        return self.max_output_tokens or size.max_output_tokens
