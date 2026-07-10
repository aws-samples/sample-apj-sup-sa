"""Smoke-import every per-model package under ``batch/models/``.

Catches scaffolding regressions: a typo in a model_spec, a missing factory,
a broken __init__ — all surface here without needing a real GPU. Run cost
is essentially zero.
"""
from __future__ import annotations

import importlib
import importlib.util
import re
from pathlib import Path

import pytest

from llm_batch_deploy import BatchDeploymentPlan, ModelSpec

# (package, plan_factory_name) — must match batch/scripts/smoke_test.py.
_MODELS: list[tuple[str, str]] = [
    ("models.qwen3_8b",              "g6e_spot_single_queue"),
    ("models.mistral_small_3_2_24b", "g7e_spot_single_queue"),
    ("models.qwen3_30b_a3b",         "g7e_spot_single_queue"),
    ("models.gemma_4_31b",           "g7e_spot_single_queue"),
    ("models.medgemma_27b",          "g7e_spot_single_queue"),
    ("models.llama_4_scout_17b",     "p4d_spot_single_queue"),
    # Additional models added later:
    ("models.gpt_oss_20b",           "g7e_spot_single_queue"),
    ("models.qwen3_coder_next",      "g6e_spot_single_queue"),
]


@pytest.mark.parametrize("package,factory", _MODELS)
def test_model_package_imports_and_factory_returns_plan(
    package: str, factory: str
) -> None:
    pkg = importlib.import_module(package)
    plan_factory = getattr(pkg, factory)
    plan = plan_factory()
    assert isinstance(plan, BatchDeploymentPlan)
    assert isinstance(plan.model_spec, ModelSpec)
    assert plan.compute_environments, "plan has no compute environments"
    assert plan.compute_environments[0].instance_types, \
        "compute environment has no instance types"
    # Sanity: model_spec.resource_prefix is non-empty and lowercase.
    assert plan.model_spec.resource_prefix
    assert plan.model_spec.resource_prefix == \
        plan.model_spec.resource_prefix.lower()


@pytest.mark.parametrize("package,_factory", _MODELS)
def test_each_model_has_readme(package: str, _factory: str) -> None:
    """Every per-model dir must ship a README.md (doc convention)."""
    pkg_dir = importlib.util.find_spec(package).submodule_search_locations[0]
    readme = Path(pkg_dir) / "README.md"
    assert readme.exists(), f"{package}: README.md missing at {readme}"
    assert readme.stat().st_size > 200, f"{package}: README.md too small"


@pytest.mark.parametrize("factory", [
    "p4d_spot_single_queue",
    "p4de_spot_single_queue",
])
def test_llama_4_scout_plans_size_root_volume_above_default(factory: str) -> None:
    """Llama-4-Scout BF16 weights are ~218 GiB. The 300 GiB default would
    technically fit but leaves no room for image + AMI + cache headroom +
    an alternate-precision artifact during testing. Both Llama-4-Scout
    plans must opt up to >=400 GiB explicitly."""
    pkg = importlib.import_module("models.llama_4_scout_17b")
    plan = getattr(pkg, factory)()
    assert plan.root_volume_gib >= 400, (
        f"llama_4_scout_17b.{factory}: root_volume_gib={plan.root_volume_gib}, "
        "expected >=400 (Llama-4-Scout-17B-16E is ~218 GiB BF16)"
    )


@pytest.mark.parametrize("factory", [
    "p4d_spot_single_queue",
    "p4de_spot_single_queue",
])
def test_llama_4_scout_plans_lift_vllm_startup_timeout(factory: str) -> None:
    """Llama-4-Scout BF16 weights (~218 GiB) take 18-75 min to download from
    HuggingFace at typical 50-200 MiB/s. The default
    ``vllm_startup_timeout_seconds=900`` (15 min) would cause the driver
    (``wait_for_vllm_ready``) to give up before vLLM finishes loading,
    failing the AWS Batch job in the worst case. Both Llama-4-Scout plans
    must opt up the timeout to cover the full 218 GiB / 50 MiB/s = ~75 min
    worst case + warmup margin.

    Same shape as the CFN waiter fix (#11) — every grace period that wraps
    the HF weight download must cover the slow path; this is the AWS Batch
    driver-side equivalent.
    """
    pkg = importlib.import_module("models.llama_4_scout_17b")
    plan = getattr(pkg, factory)()
    # 218 GiB / 50 MiB/s = 4464s for download alone, plus ~3 min warmup.
    # 4500s is the floor; we expect plans to use a comfortable margin (~90 min).
    assert plan.vllm_startup_timeout_seconds >= 4500, (
        f"llama_4_scout_17b.{factory}: "
        f"vllm_startup_timeout_seconds={plan.vllm_startup_timeout_seconds}, "
        "expected >=4500 (218 GiB / 50 MiB/s = 4464s minimum + warmup)"
    )


@pytest.mark.parametrize("factory", [
    "p4d_spot_single_queue",
    "p4de_spot_single_queue",
])
def test_llama_4_scout_plans_lift_request_timeout(factory: str) -> None:
    """Audit-shape regression #14: every per-request timeout in the inference
    loop must be large enough for the slowest expected generation under load.

    Llama-4-Scout decode on TP=8 A100-40G (p4d) generates ~30-50 tok/s per
    request. With ``in_flight_per_job=64`` and ``max_tokens`` up to 4096,
    individual requests queue inside vLLM's scheduler; observed end-to-end
    wall-clock per request can exceed the framework default of 120s — the
    httpx client would mark the request as timed out, retry, and ultimately
    record a successful generation as a failure (and waste GPU work).

    Both Llama-4-Scout plans must lift ``request_timeout_seconds`` to a
    value >= 600 (10 min) so generation slack covers the slow path. This
    pins the relationship between the per-request budget and the model's
    decode rate at high concurrency.
    """
    pkg = importlib.import_module("models.llama_4_scout_17b")
    plan = getattr(pkg, factory)()
    assert plan.request_timeout_seconds >= 600, (
        f"llama_4_scout_17b.{factory}: "
        f"request_timeout_seconds={plan.request_timeout_seconds}, "
        "expected >=600 (109B-MoE decode at high in_flight_per_job can "
        "queue requests past 120s)"
    )


def test_llama_4_scout_p4d_plan_sets_fp8_kv_cache() -> None:
    """p4d.24xlarge is 8xA100-40G = 320 GiB total VRAM. After ~218 GiB BF16
    weights, a BF16 KV cache at 64K context will OOM. Llama-4-Scout's p4d
    plan must opt into ``--kv-cache-dtype fp8`` to fit. The README, model_spec
    docstring promises this — pin it as a test so the
    promise can't drift."""
    pkg = importlib.import_module("models.llama_4_scout_17b")
    plan = pkg.p4d_spot_single_queue()
    assert "--kv-cache-dtype" in plan.extra_serve_flags
    assert "fp8" in plan.extra_serve_flags


@pytest.mark.parametrize("factory", [
    "g7e_spot_single_queue",
    "g6e_spot_single_queue",
])
def test_mistral_plans_set_mistral_serve_flags(factory: str) -> None:
    """Mistral-Small-3.2-24B ships only the Mistral-native artefact (no
    HF-format weights). Without ``--tokenizer-mode mistral``,
    ``--config-format mistral``, and ``--load-format mistral`` vLLM fails
    to load with a tokenizer/config-format error. The README + plan
    docstring both promise these flags are wired; pin it so the promise
    can't silently regress."""
    pkg = importlib.import_module("models.mistral_small_3_2_24b")
    plan = getattr(pkg, factory)()
    assert "--tokenizer-mode mistral" in plan.extra_serve_flags
    assert "--config-format mistral" in plan.extra_serve_flags
    assert "--load-format mistral" in plan.extra_serve_flags


def test_smoke_test_uses_plan_derived_wait_budget() -> None:
    """Audit-shape regression #17: the smoke-test wait budget must be
    derived from the plan's vLLM startup timeout, not a fixed constant.

    The original `wait_for_completion(..., max_wait_s=2400)` (40 min)
    under-budgeted every model whose plan declares a startup timeout
    > ~1500s. For Llama-4-Scout (218 GiB BF16 weights = 75 min HF
    download = 5400s plan-level startup timeout), the smoke test would
    always TimeoutError before the AWS Batch job's container even
    finished loading weights, throwing away a ~$2 spot run on Llama
    every time someone ran it.

    The fix uses ``plan.vllm_startup_timeout_seconds`` plus fixed
    budgets for AWS Batch dispatch + ECR image pull + decode. Pin the
    relationship so a future refactor that drops the plan reference
    fails this test rather than silently re-introducing the timeout.
    """
    smoke_path = (Path(__file__).resolve().parents[1]
                  / "scripts" / "smoke_test.py")
    src = smoke_path.read_text()

    # The wait_for_completion call must reference plan.vllm_startup_timeout_seconds
    # (directly or via a max_wait_s computed from it). Pin both shapes:
    assert "plan.vllm_startup_timeout_seconds" in src, (
        "smoke_test.py must derive the wait_for_completion budget from "
        "plan.vllm_startup_timeout_seconds; otherwise Llama-4-Scout (and "
        "any future model whose startup timeout exceeds the constant) "
        "always TimeoutErrors before the job's vLLM driver can load "
        "weights."
    )
    # And the literal `max_wait_s=2400` must NOT appear (the prior bug shape).
    assert "max_wait_s=2400" not in src, (
        "smoke_test.py must not use the legacy fixed 40-min wait budget — "
        "see the audit-shape regression in test_models_registry.py"
    )


def test_smoke_test_wait_budget_covers_batch_retries() -> None:
    """Audit-shape regression #19: the smoke-test wait budget must cover
    AWS Batch's RetryStrategy.Attempts, not just one attempt.

    The JobDefinition created by ``deployer/cfn_batch.py`` sets
    ``RetryStrategy.Attempts: 2`` so that a spot interruption / Host
    EC2 fault / docker timeout during attempt 1 restarts the job from
    scratch — a fresh ECR pull AND a fresh HF weight download. For
    Llama-4-Scout that's ~85 min of work *per attempt*. If the smoke
    wait was sized for one attempt, a single spot interrupt during
    weight download would TimeoutError the smoke driver before
    attempt 2 finished loading weights — wasting the entire smoke run.

    Same retry-amplified shape that shows up whenever a wait budget wraps
    a retried operation: the fix multiplies the per-attempt budget by the
    same Attempts value the JobDefinition declares.
    """
    smoke_path = (Path(__file__).resolve().parents[1]
                  / "scripts" / "smoke_test.py")
    src = smoke_path.read_text()

    # The smoke driver must explicitly multiply per-attempt budget by an
    # AWS-Batch-Attempts factor. We pin the constant name so the next
    # refactor that hides the multiplication behind a different variable
    # has to re-establish it visibly.
    assert "_BATCH_ATTEMPTS" in src, (
        "smoke_test.py must declare _BATCH_ATTEMPTS (mirroring the "
        "JobDefinition's RetryStrategy.Attempts) and multiply the "
        "per-attempt budget by it. Otherwise a spot interrupt during "
        "attempt 1 of a slow-cold-start model would TimeoutError "
        "before attempt 2 finishes (bug #19)."
    )

    # And the JobDefinition's Attempts value must match the smoke
    # driver's _BATCH_ATTEMPTS — otherwise the retry-amplified budget
    # silently drifts from the actual retry count. Parse both sides.
    cfn_path = (Path(__file__).resolve().parents[1] / "src"
                / "llm_batch_deploy" / "deployer" / "cfn_batch.py")
    cfn_src = cfn_path.read_text()
    m_cfn = re.search(r'"Attempts":\s*(\d+)', cfn_src)
    assert m_cfn, "cfn_batch.py must declare RetryStrategy.Attempts"
    cfn_attempts = int(m_cfn.group(1))
    m_smoke = re.search(r"_BATCH_ATTEMPTS\s*=\s*(\d+)", src)
    assert m_smoke, "smoke_test.py must define _BATCH_ATTEMPTS = N"
    smoke_attempts = int(m_smoke.group(1))
    assert smoke_attempts == cfn_attempts, (
        f"smoke_test._BATCH_ATTEMPTS={smoke_attempts} must match "
        f"cfn_batch.RetryStrategy.Attempts={cfn_attempts} so the "
        "smoke wait budget exactly covers the retry-amplified worst case"
    )


@pytest.mark.parametrize("package,factory", _MODELS)
def test_plan_serve_flags_satisfy_model_required_flags(
    package: str, factory: str,
) -> None:
    """Every plan's ``extra_serve_flags`` must contain every fragment from
    its ``model_spec.required_serve_flags``. ``required_serve_flags`` holds
    the model-level flag fragments (e.g. tokenizer-mode for Mistral, which
    ships only the Mistral-native artefact) that the *model* — not any
    individual plan — needs in order to load at all. Plans can add their
    own plan-specific flags on top (e.g. ``--kv-cache-dtype fp8`` for the
    Llama-4-Scout p4d plan only — that's a hardware-level constraint for
    A100-40G, not a model-level one).

    Without this test, a future plan added for a model with required
    flags can silently omit them — the deploy would fail at vLLM startup
    with a tokenizer/config-format error or an OOM, which is much harder
    to debug than a fast-failing unit test.
    """
    pkg = importlib.import_module(package)
    plan = getattr(pkg, factory)()
    for fragment in plan.model_spec.required_serve_flags:
        assert fragment in plan.extra_serve_flags, (
            f"{package}.{factory}: model_spec.required_serve_flags includes "
            f"{fragment!r}, but plan.extra_serve_flags is "
            f"{plan.extra_serve_flags!r} (missing the fragment)"
        )
