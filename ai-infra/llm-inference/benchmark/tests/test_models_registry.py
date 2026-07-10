"""Smoke-import every per-model package under ``benchmark/models/``.

Catches scaffolding regressions across the 6-model lineup: a missing
SYSTEM_PROMPT, an empty EXPERIMENTS dict, a broken __init__, a stale
catalog cache reference. All checked without spinning up a single GPU.
"""
from __future__ import annotations

import importlib
import importlib.util

import pytest

_MODELS: list[str] = [
    "models.qwen3_8b",
    "models.mistral_small_3_2_24b",
    "models.qwen3_30b_a3b",
    "models.gemma_4_31b",
    "models.medgemma_27b",
    "models.llama_4_scout_17b",
    # Additional models added later:
    "models.gpt_oss_20b",
    "models.qwen3_coder_next",
]


@pytest.mark.parametrize("package", _MODELS)
def test_benchmark_model_package_imports(package: str) -> None:
    pkg = importlib.import_module(package)
    # Required public names.
    assert isinstance(pkg.SYSTEM_PROMPT, str) and pkg.SYSTEM_PROMPT.strip()
    assert isinstance(pkg.SEED_INPUT, str) and pkg.SEED_INPUT.strip()
    assert isinstance(pkg.EXPERIMENTS, dict) and pkg.EXPERIMENTS, \
        "EXPERIMENTS dict is empty"
    assert isinstance(pkg.INSTANCE_TYPES, list) and pkg.INSTANCE_TYPES
    assert pkg.CATALOG_CACHE.exists(), \
        f"catalog_cache.json missing at {pkg.CATALOG_CACHE}"
    # ModelSpec sanity (the per-package alias varies by model).
    spec = next(
        (v for v in vars(pkg).values()
         if hasattr(v, "hf_model_id") and hasattr(v, "served_model_name")),
        None,
    )
    assert spec is not None, "no ModelSpec exposed by package"
    assert spec.hf_model_id, "ModelSpec.hf_model_id is empty"


@pytest.mark.parametrize("package", _MODELS)
def test_each_model_has_readme(package: str) -> None:
    """Every per-model dir must ship a README.md (doc convention)."""
    pkg = importlib.import_module(package)
    pkg_dir = importlib.util.find_spec(package).submodule_search_locations[0]
    from pathlib import Path
    readme = Path(pkg_dir) / "README.md"
    assert readme.exists(), f"{package}: README.md missing at {readme}"
    assert readme.stat().st_size > 200, f"{package}: README.md too small"


@pytest.mark.parametrize("package", _MODELS)
def test_no_psychiatric_terms_in_prompts(package: str) -> None:
    """Customer-specific terms must be gone from all prompts."""
    pkg = importlib.import_module(package)
    blob = (pkg.SYSTEM_PROMPT + "\n" + pkg.SEED_INPUT).lower()
    for forbidden in (
        "depression", "panic", "psychiatric", "adhd", "ptsd", "ocd",
        "schizophren", " gad ", "drug extraction",
    ):
        assert forbidden not in blob, \
            f"{package}: forbidden term {forbidden!r} found in prompts"


@pytest.mark.parametrize("exp_id", ["exp_6", "exp_7"])
def test_llama_4_scout_experiments_lift_vllm_ready_timeout(exp_id: str) -> None:
    """Llama-4-Scout BF16 weights (~218 GiB) take 18-75 min to download from
    HuggingFace at typical 50-200 MiB/s. The DeploymentPlan default
    ``vllm_ready_timeout_s=2400`` (40 min) would cause DeploymentRunner's
    `_wait_for_vllm_ready` to give up before vLLM finishes loading, failing
    the experiment in the worst case (slow HF throughput days). Both
    Llama-4-Scout experiments must opt up to cover the worst-case download
    plus warmup margin.

    Same shape as the CFN waiter fix (#11) and the batch driver
    startup-timeout fix (#12) — every grace period that wraps the HF
    weight download must cover the slow path.
    """
    pkg = importlib.import_module("models.llama_4_scout_17b")
    plan = pkg.EXPERIMENTS[exp_id].deployment
    # 218 GiB / 50 MiB/s = 4464s for download alone, plus warmup.
    assert plan.vllm_ready_timeout_s >= 4500, (
        f"llama_4_scout_17b.{exp_id}: "
        f"vllm_ready_timeout_s={plan.vllm_ready_timeout_s}, expected >=4500 "
        "(218 GiB / 50 MiB/s = 4464s minimum + warmup)"
    )


def test_llama_4_scout_p4d_experiment_sets_fp8_kv_cache() -> None:
    """p4d.24xlarge is 8xA100-40G = 320 GiB total VRAM. After ~218 GiB BF16
    weights, a BF16 KV cache at 32K context will OOM. Llama-4-Scout's exp_6
    plan must opt into ``--kv-cache-dtype fp8`` via extra_serve_flags so
    the user-data template actually passes the flag to vLLM. The README,
    model_spec docstring and experiments.py docstring
    promise this — pin it as a test so the promise can't drift again."""
    pkg = importlib.import_module("models.llama_4_scout_17b")
    plan = pkg.EXPERIMENTS["exp_6"].deployment
    assert "--kv-cache-dtype" in plan.extra_serve_flags
    assert "fp8" in plan.extra_serve_flags


@pytest.mark.parametrize("exp_id", ["exp_1", "exp_2", "exp_3",
                                    "exp_4", "exp_5", "exp_6", "exp_7"])
def test_mistral_experiments_set_mistral_serve_flags(exp_id: str) -> None:
    """Mistral-Small-3.2-24B ships only the Mistral-native artefact (no
    HF-format weights). vLLM needs ``--tokenizer-mode mistral``,
    ``--config-format mistral``, and ``--load-format mistral`` or it fails
    to load. The README + the experiments.py docstring
    promise this — pin it across every experiment so a future
    ExperimentConfig added without these flags surfaces immediately."""
    pkg = importlib.import_module("models.mistral_small_3_2_24b")
    plan = pkg.EXPERIMENTS[exp_id].deployment
    assert "--tokenizer-mode mistral" in plan.extra_serve_flags
    assert "--config-format mistral" in plan.extra_serve_flags
    assert "--load-format mistral" in plan.extra_serve_flags


@pytest.mark.parametrize("package", _MODELS)
def test_experiment_serve_flags_satisfy_model_required_flags(
    package: str,
) -> None:
    """For every experiment in every per-model package, the deployment plan's
    ``extra_serve_flags`` must contain every fragment from the model spec's
    ``required_serve_flags``. ``required_serve_flags`` holds the model-level
    flag fragments (e.g. tokenizer-mode for Mistral, which ships only the
    Mistral-native artefact) that the *model* — not any individual experiment
    — needs in order to load at all. Plan-specific flags (e.g.
    ``--kv-cache-dtype fp8`` only on A100-40G) belong on the plan, not here.

    Without this test, a future experiment added for a model with required
    flags can silently omit them; the deploy would fail at vLLM startup with
    a tokenizer/config-format error or an OOM, which is much harder to debug
    than a fast-failing unit test."""
    pkg = importlib.import_module(package)
    for exp_id, exp in pkg.EXPERIMENTS.items():
        plan = exp.deployment
        for fragment in exp.model_spec.required_serve_flags:
            assert fragment in plan.extra_serve_flags, (
                f"{package}.{exp_id}: model_spec.required_serve_flags includes "
                f"{fragment!r}, but plan.extra_serve_flags is "
                f"{plan.extra_serve_flags!r} (missing the fragment)"
            )
