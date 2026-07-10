"""Unit tests for configuration validation (no AWS calls)."""

from __future__ import annotations

import pytest

from bedrock_bench.config import BenchmarkConfig, Tier, Transport


def test_defaults_are_valid():
    cfg = BenchmarkConfig()
    assert cfg.profile is None  # resolved via boto3 chain, not hardcoded
    assert Tier.DEFAULT in cfg.tiers and Tier.FLEX in cfg.tiers and Tier.PRIORITY in cfg.tiers
    assert set(cfg.transports) == {Transport.INVOKE, Transport.MANTLE}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"n_requests": 0},
        {"interval_seconds": -1},
        {"max_tokens": 0},
        {"regions": ()},
        {"regions": ("not-a-region",)},
        {"regions": ("us-east-1", "bogus")},
    ],
)
def test_invalid_config_raises(kwargs):
    with pytest.raises(ValueError):
        BenchmarkConfig(**kwargs)


@pytest.mark.parametrize("region", ["us-east-1", "us-west-2", "ap-southeast-2", "eu-central-1"])
def test_valid_regions_accepted(region):
    cfg = BenchmarkConfig(regions=(region,))
    assert cfg.regions == (region,)


def test_tier_default_flag():
    assert Tier.DEFAULT.is_default
    assert not Tier.FLEX.is_default
    assert not Tier.PRIORITY.is_default
