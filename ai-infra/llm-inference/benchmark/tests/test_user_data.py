"""Tests for the UserDataRenderer (schema v2 — takes a Catalog at render time).

Renders each Jinja2 template with a representative config + mocked catalog,
asserts no unresolved tokens remain, and validates that the key shell
fragments are present.
"""
from __future__ import annotations

import pytest
from jinja2 import UndefinedError

from vllm_ec2_bench import (
    Catalog,
    DeploymentPlan,
    ExperimentConfig,
    HardwareFacts,
    ModelSpec,
)
from vllm_ec2_bench.deployer.user_data import UserDataRenderer


def _ms() -> ModelSpec:
    return ModelSpec(
        resource_prefix="test-model",
        display_name="Test Model",
        hf_model_id="org/test-model-27b",
        served_model_name="test-model-27b",
        weight_size_gib=55.0,
        default_max_model_len=16384,
        gated=True,
    )


# Minimal hardware facts for the three instances we exercise.
def _mock_catalog() -> Catalog:
    facts_by_type = {
        "p4de.24xlarge": HardwareFacts(
            instance_type="p4de.24xlarge", family="gpu",
            accelerator_model="NVIDIA A100 80GB", accelerator_architecture="Ampere",
            num_accelerators=8, vram_gib_per_accelerator=80.0,
            vcpu=96, ram_gib=1152,
        ),
        "p5e.48xlarge": HardwareFacts(
            instance_type="p5e.48xlarge", family="gpu",
            accelerator_model="NVIDIA H200", accelerator_architecture="Hopper (H200)",
            num_accelerators=8, vram_gib_per_accelerator=141.0,
            vcpu=192, ram_gib=2048,
        ),
        "inf2.24xlarge": HardwareFacts(
            instance_type="inf2.24xlarge", family="neuron",
            accelerator_model="AWS Inferentia2", accelerator_architecture="Neuron 2nd gen",
            num_accelerators=6, vram_gib_per_accelerator=32.0,
            vcpu=96, ram_gib=384,
        ),
    }
    cat = Catalog(cache_path="/tmp/_unused_catalog_cache.json")
    cat._hardware = facts_by_type  # noqa: SLF001
    cat._prices = {}               # noqa: SLF001
    cat._meta = {"schema": 2}      # noqa: SLF001
    cat._loaded = True             # noqa: SLF001
    return cat


def _plan(instance_type: str, **kwargs) -> DeploymentPlan:
    defaults = {
        "experiment_id": "exp_test",
        "instance_type": instance_type,
        "region": "us-east-2",
        "capacity_preference": ["spot", "on-demand", "odcr"],
    }
    defaults.update(kwargs)
    return DeploymentPlan(**defaults)


def _gpu_no_mig_cfg() -> ExperimentConfig:
    return ExperimentConfig(
        model_spec=_ms(),
        deployment=_plan("p4de.24xlarge", tensor_parallel=1, data_parallel=8),
    )


def _gpu_mig_cfg() -> ExperimentConfig:
    return ExperimentConfig(
        model_spec=_ms(),
        deployment=_plan(
            "p5e.48xlarge",
            tensor_parallel=1, data_parallel=16,
            mig_profile="3g.71gb", mig_replicas_per_gpu=2,
            capacity_preference=["capacity-block"],
        ),
    )


def _neuron_cfg() -> ExperimentConfig:
    return ExperimentConfig(
        model_spec=_ms(),
        deployment=_plan("inf2.24xlarge", tensor_parallel=6, data_parallel=1),
    )


class TestUserDataRenderer:
    def test_gpu_no_mig_renders_clean(self) -> None:
        cfg = _gpu_no_mig_cfg()
        cat = _mock_catalog()
        r = UserDataRenderer()
        ud = r.render(cfg, cat, hf_secret_name="medgemma-27b-benchmark/hf-token", vllm_api_key="api-key-xxx")  # nosec B106

        assert "{{" not in ud and "{%" not in ud
        assert "--tensor-parallel-size 1" in ud
        assert "--data-parallel-size 8" in ud
        assert "--pipeline-parallel-size 1" in ud
        assert "--gpus all" in ud
        assert "nvidia-smi -mig 1" not in ud
        assert "org/test-model-27b" in ud
        assert "test-model-27b" in ud
        assert "aws secretsmanager get-secret-value" in ud
        assert "medgemma-27b-benchmark/hf-token" in ud
        # The token VALUE must never be Jinja-substituted in
        assert "hf_fake_token" not in ud
        assert "api-key-xxx" in ud
        assert "test-model-vllm" in ud  # container name

    def test_neuron_renders_clean(self) -> None:
        cfg = _neuron_cfg()
        cat = _mock_catalog()
        r = UserDataRenderer()
        ud = r.render(cfg, cat, hf_secret_name=None, vllm_api_key="api-neuron")

        assert "{{" not in ud and "{%" not in ud
        assert "vllm serve" in ud
        assert "NEURON_RT_VISIBLE_CORES" in ud
        assert "--tensor-parallel-size 6" in ud
        # --data-parallel-size is a vllm-openai flag; vllm-neuronx uses tp+pp only.
        cli_section = ud.split("python -m vllm.entrypoints")[1] if "python -m vllm.entrypoints" in ud else ""
        assert "--data-parallel-size" not in cli_section
        assert "NEURON_DEVICE_FLAGS=" in ud
        assert "for i in $(seq 0 $((6 - 1)))" in ud
        assert "--device=/dev/neuron$i" in ud
        assert "-e HF_TOKEN=" not in ud

    def test_strict_undefined_catches_missing_vars(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        r = UserDataRenderer()
        cat = _mock_catalog()
        original = r._build_context
        def _sabotaged(*args, **kwargs):
            ctx = original(*args, **kwargs)
            ctx.pop("model_id", None)
            return ctx
        monkeypatch.setattr(r, "_build_context", _sabotaged)
        with pytest.raises(UndefinedError, match="model_id"):
            r.render(_gpu_no_mig_cfg(), cat, hf_secret_name="medgemma-27b-benchmark/hf-token", vllm_api_key="y")  # nosec B106

    def test_render_b64_is_valid_base64(self) -> None:
        import base64
        r = UserDataRenderer()
        cat = _mock_catalog()
        b64 = r.render_b64(_gpu_no_mig_cfg(), cat, hf_secret_name="medgemma-27b-benchmark/hf-token", vllm_api_key="k")  # nosec B106
        decoded = base64.b64decode(b64).decode()
        assert decoded.startswith("#!/bin/bash")

    def test_hf_token_omitted_when_empty(self) -> None:
        cfg = _gpu_no_mig_cfg()
        cat = _mock_catalog()
        r = UserDataRenderer()
        ud = r.render(cfg, cat, hf_secret_name=None, vllm_api_key="k")
        assert "-e HF_TOKEN=" not in ud
        assert "-e HUGGING_FACE_HUB_TOKEN=" not in ud

    def test_extra_serve_flags_default_absent(self) -> None:
        """A plan that doesn't set extra_serve_flags renders without
        --kv-cache-dtype anywhere in the script."""
        cfg = _gpu_no_mig_cfg()
        cat = _mock_catalog()
        r = UserDataRenderer()
        ud = r.render(cfg, cat, hf_secret_name=None, vllm_api_key="k")
        assert "--kv-cache-dtype" not in ud

    def test_extra_serve_flags_propagate_to_user_data(self) -> None:
        """extra_serve_flags on the plan must surface verbatim in the
        rendered vllm serve command line. This is the only path that gets
        --kv-cache-dtype fp8 onto Llama-4-Scout p4d benchmarks."""
        plan = _plan(
            "p4de.24xlarge",
            tensor_parallel=8, data_parallel=1,
            extra_serve_flags="--kv-cache-dtype fp8",
        )
        cfg = ExperimentConfig(model_spec=_ms(), deployment=plan)
        cat = _mock_catalog()
        r = UserDataRenderer()
        ud = r.render(cfg, cat, hf_secret_name=None, vllm_api_key="k")
        assert "--kv-cache-dtype fp8" in ud
