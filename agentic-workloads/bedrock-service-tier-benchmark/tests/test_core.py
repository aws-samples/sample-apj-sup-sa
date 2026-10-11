"""Unit tests for config, prompts, metrics, registry and cell expansion (no AWS calls)."""

from __future__ import annotations

import random

import pytest

from bedrock_bench.cells import expand_cells
from bedrock_bench.config import Api, BenchmarkConfig, CacheMode, PromptSize, Tier, validate_region
from bedrock_bench.metrics import MetricStats, bootstrap_delta, summarize
from bedrock_bench.prompts import PromptFactory
from bedrock_bench.registry import spec_from_dict, spec_to_dict

GLM = {
    "key": "zai.glm-5.3",
    "family": "GLM (Z.AI)",
    "display_name": "GLM 5.3",
    "reasoning": True,
    "cache": {"implicit": True, "explicit": True, "min_tokens": 1024},
    "offerings": [
        {
            "endpoint": "runtime",
            "api": "converse_stream",
            "scope": "geo",
            "model_id": "us.zai.glm-5.3",
            "regions": ["us-east-1", "us-west-2"],
            "tiers": ["default", "flex", "priority"],
        },
        {
            "endpoint": "runtime",
            "api": "chat_completions",
            "scope": "global",
            "model_id": "global.zai.glm-5.3",
            "regions": ["us-east-1"],
            "tiers": ["default"],
        },
    ],
}


# --------------------------------------------------------------------------- config
@pytest.mark.parametrize("region", ["us-east-1", "ap-southeast-2", "us-gov-west-1", "cn-north-1"])
def test_valid_regions(region):
    assert validate_region(region) == region


@pytest.mark.parametrize("region", ["", "us-east-1.evil.com", "US-EAST-1", "us-east"])
def test_invalid_regions(region):
    with pytest.raises(ValueError):
        validate_region(region)


def test_config_accepts_strings_and_validates():
    c = BenchmarkConfig(tiers=("default", "flex"), apis=("responses",))
    assert c.tiers == (Tier.DEFAULT, Tier.FLEX)
    assert c.apis == (Api.RESPONSES,)
    assert c.timeout_for(Tier.FLEX) == c.flex_timeout_seconds
    with pytest.raises(ValueError):
        BenchmarkConfig(n_requests=0)
    with pytest.raises(ValueError):
        BenchmarkConfig(apis=())


# --------------------------------------------------------------------------- prompts
def test_cold_prompts_are_unique_and_flagged():
    f = PromptFactory(PromptSize.SMALL, CacheMode.COLD, seed=1, cell_key="k")
    a, b = f.next(), f.next()
    assert a.expect_cold and b.expect_cold
    assert a.document.splitlines()[0] != b.document.splitlines()[0]
    assert a.document.startswith("Request ")


def test_warm_prompts_share_prefix_but_not_question():
    f = PromptFactory(PromptSize.SMALL, CacheMode.WARM_IMPLICIT, seed=1, cell_key="k")
    a, b = f.next(), f.next()
    assert a.document == b.document
    assert a.question != b.question
    assert not a.expect_cold


def test_prompt_sizes_scale():
    lens = {
        s: len(PromptFactory(s, CacheMode.COLD, 1, "k").next().document.split()) for s in PromptSize
    }
    assert lens[PromptSize.SMALL] < lens[PromptSize.MEDIUM] < lens[PromptSize.LARGE]


# --------------------------------------------------------------------------- metrics
def test_metric_stats_ignores_missing():
    s = MetricStats.from_values([1.0, None, 3.0, float("nan")])
    assert s.n == 2 and s.p50 == 2.0


def test_bootstrap_detects_real_difference_only():
    r = random.Random(3)
    a = [1 + r.random() * 0.1 for _ in range(30)]
    slow = [2 + r.random() * 0.1 for _ in range(30)]
    same = [1 + r.random() * 0.1 for _ in range(30)]
    assert bootstrap_delta(a, slow, metric="e2e", seed=1).significant is True
    assert bootstrap_delta(a, same, metric="e2e", seed=1).significant is False
    assert bootstrap_delta([1.0], [2.0], metric="e2e").delta is None  # too few samples


def test_summarize_counts_exclusions_and_errors():
    samples = [
        {
            "ttft": 1.0,
            "e2e": 2.0,
            "itl": 0.01,
            "error": None,
            "included": True,
            "usage": {"cache_read_tokens": 0, "input_tokens": 100, "output_tokens": 50},
        },
        {
            "ttft": 1.0,
            "e2e": 2.0,
            "error": None,
            "included": False,
            "exclude_reason": "cache_contaminated",
            "usage": {"cache_read_tokens": 90},
        },
        {"error": "ThrottlingException: slow down", "error_kind": "throttled"},
    ]
    s = summarize({"label": "x", "tier": "default"}, samples)
    assert s.succeeded == 2 and s.failed == 1
    assert s.excluded == {"cache_contaminated": 1}
    assert s.errors == {"throttled": 1}
    assert s.stats["e2e"].n == 1
    assert s.cache_hit_rate == 0.5


# --------------------------------------------------------------------------- registry + cells
def test_registry_round_trip():
    spec = spec_from_dict(GLM)
    assert spec_from_dict(spec_to_dict(spec)) == spec
    assert spec.multi_tier


def test_expand_cells_keeps_only_comparable_contexts():
    spec = spec_from_dict(GLM)
    cfg = BenchmarkConfig(
        apis=("converse_stream", "chat_completions"),
        cache_modes=("cold", "warm_implicit"),
        tiers=("default", "flex"),
    )
    cells = expand_cells(cfg, [spec])
    # chat_completions/global serves only default -> no comparison -> dropped.
    assert {c.api for c in cells} == {Api.CONVERSE_STREAM}
    # 2 cache modes x 2 tiers (priority not requested).
    assert len(cells) == 4
    assert {c.region for c in cells} == {"us-east-1"}
    assert len({c.domain for c in cells}) == 1


def test_warm_cells_skipped_when_prefix_below_cache_minimum():
    d = dict(GLM, cache={"implicit": True, "explicit": True, "min_tokens": 4096})
    cfg = BenchmarkConfig(apis=("converse_stream",), cache_modes=("warm_implicit",))
    assert expand_cells(cfg, [spec_from_dict(d)]) == []
