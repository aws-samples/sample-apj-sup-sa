"""Tests for cell expansion: per-transport tier support and baseline inclusion."""

from __future__ import annotations

from bedrock_bench.cells import expand_cells
from bedrock_bench.config import BenchmarkConfig, Tier, Transport
from bedrock_bench.registry import ModelSpec

# A synthetic spec: invoke serves default+flex+priority; mantle serves default+flex only.
SPEC = ModelSpec(
    key="vendor.model",
    family="Vendor",
    display_name="Vendor Model",
    invoke_id="vendor.model-v1:0",
    invoke_region="us-west-2",
    invoke_tiers=(Tier.DEFAULT, Tier.FLEX, Tier.PRIORITY),
    mantle_id="vendor.model",
    mantle_region="us-west-2",
    mantle_tiers=(Tier.DEFAULT, Tier.FLEX),
)

# A spec that serves no non-default tier anywhere — must be excluded entirely.
DEFAULT_ONLY = ModelSpec(
    key="vendor.boring",
    family="Vendor",
    display_name="Boring",
    invoke_id="vendor.boring",
    invoke_region="us-west-2",
    invoke_tiers=(Tier.DEFAULT,),
    mantle_id=None,
    mantle_region=None,
    mantle_tiers=(),
)


def _labels(cells):
    return {c.label for c in cells}


def test_expand_includes_supported_tiers_with_baseline():
    cfg = BenchmarkConfig(regions=("us-west-2",))
    cells = expand_cells(cfg, [SPEC])
    labels = _labels(cells)
    # invoke serves all three tiers
    assert "vendor.model|invoke|default|us-west-2" in labels
    assert "vendor.model|invoke|flex|us-west-2" in labels
    assert "vendor.model|invoke|priority|us-west-2" in labels
    # mantle serves default+flex only — no priority cell
    assert "vendor.model|mantle|priority|us-west-2" not in labels
    assert "vendor.model|mantle|flex|us-west-2" in labels
    assert "vendor.model|mantle|default|us-west-2" in labels


def test_default_only_model_excluded():
    cfg = BenchmarkConfig(regions=("us-west-2",))
    cells = expand_cells(cfg, [DEFAULT_ONLY])
    assert cells == []


def test_tier_filter_still_keeps_default_baseline():
    # Requesting only priority should still pull in default as the comparison baseline.
    cfg = BenchmarkConfig(regions=("us-west-2",), tiers=(Tier.PRIORITY,))
    cells = expand_cells(cfg, [SPEC])
    labels = _labels(cells)
    assert "vendor.model|invoke|priority|us-west-2" in labels
    assert "vendor.model|invoke|default|us-west-2" in labels  # baseline auto-added
    # mantle has no priority -> mantle should contribute nothing here
    assert not any("mantle" in label for label in labels)


def test_transport_filter():
    cfg = BenchmarkConfig(regions=("us-west-2",), transports=(Transport.INVOKE,))
    cells = expand_cells(cfg, [SPEC])
    assert all(c.transport is Transport.INVOKE for c in cells)
