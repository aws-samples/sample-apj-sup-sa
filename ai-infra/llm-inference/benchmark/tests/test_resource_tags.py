"""Tag-shape tests for ResourceManager.

Resource-tag policy:  every AWS resource benchmark provisions carries
``Project=<...>`` AND ``Model=<resource_prefix>`` tags so cleanup automation
can sweep all benchmark resources for a given model. The two paths
(_base_tags for ec2/sg, iam_tags inline in _ensure_instance_profile) must
both carry both tags.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vllm_ec2_bench.deployer.resources import ResourceManager  # noqa: E402


def _make_mgr(resource_prefix: str = "qwen3-8b") -> ResourceManager:
    cfg = MagicMock()
    cfg.model_spec.project_tag_value = f"{resource_prefix}-benchmark"
    cfg.model_spec.resource_prefix = resource_prefix
    cfg.deployment.experiment_id = "exp_3_g7e_2xl"
    return ResourceManager(
        config=cfg,
        catalog=MagicMock(),
        ec2_client=MagicMock(),
        iam_client=MagicMock(),
    )


def test_base_tags_carry_project_and_model():
    mgr = _make_mgr("qwen3-8b")
    tags = {t["Key"]: t["Value"] for t in mgr._base_tags()}
    assert tags["Project"] == "qwen3-8b-benchmark"
    assert tags["Model"] == "qwen3-8b", (
        "_base_tags must carry Model=<resource_prefix> so cleanup automation "
        "can sweep all benchmark resources for a given model."
    )
    assert tags["Experiment"] == "exp_3_g7e_2xl"


def test_iam_tags_inline_in_ensure_instance_profile_carry_both():
    """The IAM role + instance profile creation builds its own tags list
    rather than calling _base_tags (which includes Experiment, an IAM-irrelevant
    dimension). The audit checks the inline iam_tags list literal carries
    both Project and Model."""
    src = inspect.getsource(ResourceManager._ensure_instance_profile)
    # The literal must mention both Project and Model tags. A simple string
    # check is enough since the iam_tags list is a top-level local in the
    # method (no conditional branches over tag composition).
    assert '"Key": "Project"' in src
    assert '"Key": "Model"' in src, (
        "_ensure_instance_profile's IAM tag list must include "
        "Model=<resource_prefix> for per-model tagging "
        "for cleanup discoverability."
    )
    assert 'ms.resource_prefix' in src, (
        "Model tag value must derive from ms.resource_prefix, not a "
        "hardcoded string."
    )
