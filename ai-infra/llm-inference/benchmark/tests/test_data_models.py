"""Validator + cross-field tests for vllm_ec2_bench.data.

Tests cover:
* HardwareFacts — field-level validation.
* ModelSpec — resource_prefix format, derived IAM names, validators.
* DeploymentPlan — structural validators (MIG profile known, no duplicate
  capacity modes). Cross-field checks that need a catalog are tested via
  ExperimentConfig.validate_against(catalog).
* ExperimentConfig — weight-fit + TP*DP*PP checks against a Catalog.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from vllm_ec2_bench import (
    Catalog,
    DeploymentPlan,
    ExperimentConfig,
    HardwareFacts,
    ModelSpec,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _a100_40gb_facts() -> HardwareFacts:
    return HardwareFacts(
        instance_type="p4d.24xlarge",
        family="gpu",
        accelerator_model="NVIDIA A100 40GB",
        accelerator_architecture="Ampere",
        num_accelerators=8,
        vram_gib_per_accelerator=40.0,
        vcpu=96,
        ram_gib=1152,
    )


def _h200_facts() -> HardwareFacts:
    return HardwareFacts(
        instance_type="p5e.48xlarge",
        family="gpu",
        accelerator_model="NVIDIA H200",
        accelerator_architecture="Hopper (H200)",
        num_accelerators=8,
        vram_gib_per_accelerator=141.0,
        vcpu=192,
        ram_gib=2048,
    )


def _inf2_facts() -> HardwareFacts:
    return HardwareFacts(
        instance_type="inf2.24xlarge",
        family="neuron",
        accelerator_model="AWS Inferentia2",
        accelerator_architecture="Neuron 2nd gen",
        num_accelerators=6,
        vram_gib_per_accelerator=32.0,
        vcpu=96,
        ram_gib=384,
    )


def _medgemma_27b() -> ModelSpec:
    return ModelSpec(
        resource_prefix="medgemma-27b",
        display_name="MedGemma 27B",
        hf_model_id="google/medgemma-27b-text-it",
        served_model_name="medgemma-27b",
        weight_size_gib=55.0,
        gated=True,
    )


def _mock_catalog(facts_by_type: dict[str, HardwareFacts]) -> Catalog:
    """Build a populated Catalog without touching disk or AWS."""
    cat = Catalog(cache_path="/tmp/_unused_catalog_cache.json")
    cat._hardware = dict(facts_by_type)  # noqa: SLF001
    cat._prices = {}  # noqa: SLF001
    cat._meta = {"schema": 2}  # noqa: SLF001
    cat._loaded = True  # noqa: SLF001
    return cat


# ---------------------------------------------------------------------------
# HardwareFacts
# ---------------------------------------------------------------------------
class TestHardwareFacts:
    def test_happy_path(self) -> None:
        hf = _a100_40gb_facts()
        assert hf.vram_gib_total == pytest.approx(320.0)
        assert hf.family == "gpu"

    def test_zero_accelerators_rejected(self) -> None:
        with pytest.raises(ValidationError, match="greater than 0"):
            HardwareFacts(
                instance_type="fake.1xlarge", family="gpu",
                accelerator_model="X", accelerator_architecture="Y",
                num_accelerators=0, vram_gib_per_accelerator=10.0,
                vcpu=4, ram_gib=8,
            )

    def test_invalid_instance_type_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid EC2 instance_type"):
            HardwareFacts(
                instance_type="nothing-here", family="gpu",
                accelerator_model="X", accelerator_architecture="Y",
                num_accelerators=1, vram_gib_per_accelerator=10.0,
                vcpu=4, ram_gib=8,
            )

    def test_frozen(self) -> None:
        hf = _a100_40gb_facts()
        with pytest.raises(ValidationError):
            hf.num_accelerators = 16  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ModelSpec — unchanged from before
# ---------------------------------------------------------------------------
class TestModelSpec:
    def test_happy_path(self) -> None:
        ms = _medgemma_27b()
        assert ms.iam_role_name == "medgemma-27b-benchmark-role"
        assert ms.iam_instance_profile_name == "medgemma-27b-benchmark-instance-profile"
        assert ms.project_tag_value == "medgemma-27b-benchmark"
        assert ms.container_name == "medgemma-27b-vllm"

    def test_invalid_resource_prefix(self) -> None:
        bad = ["MedGemma_27B", "Medgemma", "a", "-foo", "foo-", "foo/bar", "x" * 41]
        for p in bad:
            with pytest.raises(ValidationError, match="resource_prefix"):
                ModelSpec(
                    resource_prefix=p, display_name="X",
                    hf_model_id="org/x", served_model_name="x",
                    weight_size_gib=1.0,
                )

    def test_invalid_hf_id(self) -> None:
        with pytest.raises(ValidationError, match="hf_model_id"):
            ModelSpec(
                resource_prefix="foo", display_name="X",
                hf_model_id="no-slash", served_model_name="x", weight_size_gib=1.0,
            )

    def test_frozen(self) -> None:
        ms = _medgemma_27b()
        with pytest.raises(ValidationError):
            ms.weight_size_gib = 60.0  # type: ignore[misc]

    def test_default_vllm_gpu_image_supports_gemma4(self) -> None:
        # Gemma 4 (released Apr 2026) requires vLLM >= 0.11.0; v0.10.2 fails
        # at startup with a transformers ValidationError on the gemma4 type.
        ms = _medgemma_27b()
        tag = ms.vllm_gpu_image.split(":")[1]
        assert tag.startswith("v0."), f"unexpected tag shape: {tag}"
        major_minor = tag.lstrip("v").split(".")
        version_tuple = (int(major_minor[0]), int(major_minor[1]))
        assert version_tuple >= (0, 11), (
            f"vllm image {ms.vllm_gpu_image} predates gemma4 support; need >=v0.11.0"
        )


# ---------------------------------------------------------------------------
# DeploymentPlan — structural validators only
# ---------------------------------------------------------------------------
class TestDeploymentPlan:
    def test_happy_no_mig(self) -> None:
        dp = DeploymentPlan(
            experiment_id="exp_1",
            instance_type="p4d.24xlarge",
            tensor_parallel=2, data_parallel=4, pipeline_parallel=1,
            region="us-east-2",
            capacity_preference=["spot", "on-demand", "odcr"],
        )
        assert dp.model_replicas == 4
        assert dp.mig_profile_ids is None

    def test_happy_with_mig(self) -> None:
        dp = DeploymentPlan(
            experiment_id="exp_9",
            instance_type="p5e.48xlarge",
            tensor_parallel=1, data_parallel=16, pipeline_parallel=1,
            mig_profile="3g.71gb", mig_replicas_per_gpu=2,
            region="us-east-2",
            capacity_preference=["capacity-block"],
        )
        assert dp.mig_profile_ids == (9, 0)

    def test_unknown_mig_profile_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Unknown MIG profile"):
            DeploymentPlan(
                experiment_id="exp_bad",
                instance_type="p5e.48xlarge",
                tensor_parallel=1, data_parallel=16,
                mig_profile="9g.999gb", mig_replicas_per_gpu=2,
                region="us-east-2",
                capacity_preference=["capacity-block"],
            )

    def test_duplicate_capacity_modes_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not contain duplicates"):
            DeploymentPlan(
                experiment_id="exp_bad",
                instance_type="p4d.24xlarge",
                tensor_parallel=2, data_parallel=4,
                region="us-east-2",
                capacity_preference=["spot", "spot", "on-demand"],
            )

    def test_empty_capacity_preference_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DeploymentPlan(
                experiment_id="exp_bad",
                instance_type="p4d.24xlarge",
                tensor_parallel=2, data_parallel=4,
                region="us-east-2",
                capacity_preference=[],
            )

    def test_invalid_instance_type_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid EC2 instance_type"):
            DeploymentPlan(
                experiment_id="exp_bad",
                instance_type="nonsense",
                tensor_parallel=1, data_parallel=1,
                region="us-east-2",
                capacity_preference=["spot"],
            )


# ---------------------------------------------------------------------------
# DeploymentPlan.validate_against(catalog) + ExperimentConfig.validate_against
# ---------------------------------------------------------------------------
class TestValidateAgainstCatalog:
    def _plan(self, instance_type: str, **overrides) -> DeploymentPlan:
        defaults = dict(
            experiment_id="exp_test",
            instance_type=instance_type,
            tensor_parallel=1, data_parallel=1, pipeline_parallel=1,
            region="us-east-2",
            capacity_preference=["spot"],
        )
        defaults.update(overrides)
        return DeploymentPlan(**defaults)

    def test_tp_dp_pp_mismatch_rejected(self) -> None:
        cat = _mock_catalog({"p4d.24xlarge": _a100_40gb_facts()})
        plan = self._plan(
            "p4d.24xlarge", tensor_parallel=3, data_parallel=2, pipeline_parallel=1,
        )  # 3×2 = 6 ≠ 8 GPUs
        with pytest.raises(ValueError, match="Parallelism mismatch"):
            plan.validate_against(cat)

    def test_mig_only_on_gpu_rejected(self) -> None:
        cat = _mock_catalog({"inf2.24xlarge": _inf2_facts()})
        plan = self._plan(
            "inf2.24xlarge",
            tensor_parallel=6, data_parallel=1, mig_profile="3g.20gb",
            mig_replicas_per_gpu=1,
            capacity_preference=["spot"],
        )
        with pytest.raises(ValueError, match="mig_profile is GPU-only"):
            plan.validate_against(cat)

    def test_mig_replicas_without_profile_rejected(self) -> None:
        cat = _mock_catalog({"p4d.24xlarge": _a100_40gb_facts()})
        plan = self._plan(
            "p4d.24xlarge",
            tensor_parallel=1, data_parallel=16,
            mig_replicas_per_gpu=2,  # but mig_profile is None
        )
        with pytest.raises(ValueError, match="mig_replicas_per_gpu must be 1"):
            plan.validate_against(cat)

    def test_happy_passes(self) -> None:
        cat = _mock_catalog({"p4d.24xlarge": _a100_40gb_facts()})
        plan = self._plan(
            "p4d.24xlarge", tensor_parallel=2, data_parallel=4, pipeline_parallel=1,
        )
        # Should not raise
        plan.validate_against(cat)


class TestExperimentConfigValidation:
    def _plan(self, it: str, **kw) -> DeploymentPlan:
        defaults = dict(
            experiment_id="exp_x",
            instance_type=it,
            tensor_parallel=1, data_parallel=1, pipeline_parallel=1,
            region="us-east-2",
            capacity_preference=["spot"],
        )
        defaults.update(kw)
        return DeploymentPlan(**defaults)

    def test_weight_fit_passes_on_p4de(self) -> None:
        facts = HardwareFacts(
            instance_type="p4de.24xlarge", family="gpu",
            accelerator_model="NVIDIA A100 80GB", accelerator_architecture="Ampere",
            num_accelerators=8, vram_gib_per_accelerator=80.0,
            vcpu=96, ram_gib=1152,
        )
        cat = _mock_catalog({"p4de.24xlarge": facts})
        cfg = ExperimentConfig(
            model_spec=_medgemma_27b(),
            deployment=self._plan("p4de.24xlarge", tensor_parallel=1, data_parallel=8),
        )
        cfg.validate_against(cat)  # should not raise

    def test_weight_fit_fails_on_tiny_gpu(self) -> None:
        tiny = HardwareFacts(
            instance_type="g5.xlarge", family="gpu",
            accelerator_model="NVIDIA A10G", accelerator_architecture="Ampere",
            num_accelerators=1, vram_gib_per_accelerator=22.4,
            vcpu=4, ram_gib=16,
        )
        cat = _mock_catalog({"g5.xlarge": tiny})
        cfg = ExperimentConfig(
            model_spec=_medgemma_27b(),
            deployment=self._plan("g5.xlarge"),
        )
        with pytest.raises(ValueError, match="weight-fit check failed"):
            cfg.validate_against(cat)

    def test_weight_fit_passes_on_h200_mig(self) -> None:
        cat = _mock_catalog({"p5e.48xlarge": _h200_facts()})
        cfg = ExperimentConfig(
            model_spec=_medgemma_27b(),
            deployment=self._plan(
                "p5e.48xlarge",
                tensor_parallel=1, data_parallel=16,
                mig_profile="3g.71gb", mig_replicas_per_gpu=2,
                capacity_preference=["capacity-block"],
            ),
        )
        cfg.validate_against(cat)  # should not raise

    def test_weight_fit_fails_on_mig_profile_too_small(self) -> None:
        # A100-40GB 3g.20gb slice is too small for MedGemma 55 GB
        cat = _mock_catalog({"p4d.24xlarge": _a100_40gb_facts()})
        cfg = ExperimentConfig(
            model_spec=_medgemma_27b(),
            deployment=self._plan(
                "p4d.24xlarge",
                tensor_parallel=1, data_parallel=16,
                mig_profile="3g.20gb", mig_replicas_per_gpu=2,
                capacity_preference=["capacity-block"],
            ),
        )
        with pytest.raises(ValueError, match="weight-fit check failed"):
            cfg.validate_against(cat)


# ---------------------------------------------------------------------------
# ExperimentConfig derived properties + price_per_replica
# ---------------------------------------------------------------------------
class TestExperimentConfigPricing:
    def _cat_with_prices(
        self, facts: HardwareFacts, prices: dict[str, float] | None,
    ) -> Catalog:
        cat = Catalog(cache_path="/tmp/_unused_catalog_cache.json")
        cat._hardware = {facts.instance_type: facts}           # noqa: SLF001
        cat._prices = {facts.instance_type: dict(prices or {})}  # noqa: SLF001
        cat._meta = {"schema": 2}                               # noqa: SLF001
        cat._loaded = True                                      # noqa: SLF001
        return cat

    def test_price_per_replica_region_scoped(self) -> None:
        facts = HardwareFacts(
            instance_type="p4de.24xlarge", family="gpu",
            accelerator_model="NVIDIA A100 80GB", accelerator_architecture="Ampere",
            num_accelerators=8, vram_gib_per_accelerator=80.0,
            vcpu=96, ram_gib=1152,
        )
        cat = self._cat_with_prices(facts, {"us-east-1": 27.44705, "us-west-2": 27.44705})

        cfg = ExperimentConfig(
            model_spec=_medgemma_27b(),
            deployment=DeploymentPlan(
                experiment_id="exp_west",
                instance_type="p4de.24xlarge",
                tensor_parallel=1, data_parallel=8, pipeline_parallel=1,
                region="us-west-2",
                capacity_preference=["spot"],
            ),
        )
        assert cfg.price_per_replica_usd_per_hour(cat) == pytest.approx(27.44705 / 8)

    def test_price_per_replica_none_when_region_not_cached(self) -> None:
        facts = HardwareFacts(
            instance_type="p4de.24xlarge", family="gpu",
            accelerator_model="NVIDIA A100 80GB", accelerator_architecture="Ampere",
            num_accelerators=8, vram_gib_per_accelerator=80.0,
            vcpu=96, ram_gib=1152,
        )
        cat = self._cat_with_prices(facts, {"us-east-1": 27.44705})  # only 1 region

        cfg = ExperimentConfig(
            model_spec=_medgemma_27b(),
            deployment=DeploymentPlan(
                experiment_id="exp_east2",
                instance_type="p4de.24xlarge",
                tensor_parallel=1, data_parallel=8, pipeline_parallel=1,
                region="us-east-2",  # NOT in the dict
                capacity_preference=["spot"],
            ),
        )
        assert cfg.price_per_replica_usd_per_hour(cat) is None
