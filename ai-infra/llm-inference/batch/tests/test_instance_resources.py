"""Tests for cfn_batch's INSTANCE_RESOURCES table + _resource_requirements helper.

These tests pin the table entries so a future edit that accidentally halves
memory or drops GPU count will fail CI instead of silently producing
misconfigured JobDefinitions.
"""
from __future__ import annotations

import pytest

from llm_batch_deploy.data import (
    BatchDeploymentPlan,
    ComputeEnvironmentConfig,
    ModelSpec,
    QueueConfig,
)
from llm_batch_deploy.deployer.cfn import build_template
from llm_batch_deploy.deployer.cfn_batch import (
    INSTANCE_RESOURCES,
    _resource_requirements,
    job_definition,
)


def _ms() -> ModelSpec:
    return ModelSpec(
        resource_prefix="medgemma-27b",
        hf_model_id="google/medgemma-27b-text-it",
        served_model_name="medgemma-27b",
        weight_size_gib=55.0,
    )


def _plan(instance_type: str, **overrides) -> BatchDeploymentPlan:
    kw = dict(
        model_spec=_ms(),
        compute_environments=[ComputeEnvironmentConfig(name_suffix="gpu", instance_types=[instance_type], capacity_mode="spot",)],
        queues=[QueueConfig(
            name_suffix="primary", priority=1,
            compute_environment_suffixes=["gpu"],
        )],
    )
    kw.update(overrides)
    return BatchDeploymentPlan(**kw)


class TestInstanceResourcesTable:
    """The values are deliberate. If these tests fail, a human must review
    the change — silent capacity changes cause container-kill cascades."""

    def test_p4d_has_8_gpus(self) -> None:
        assert INSTANCE_RESOURCES["p4d.24xlarge"]["gpus"] == 8
        assert INSTANCE_RESOURCES["p4d.24xlarge"]["vcpus"] == 92

    def test_p5_has_192_vcpus_reserved_to_188(self) -> None:
        assert INSTANCE_RESOURCES["p5.48xlarge"]["vcpus"] == 188

    def test_g7e_2xlarge_is_single_gpu(self) -> None:
        entry = INSTANCE_RESOURCES["g7e.2xlarge"]
        assert entry["gpus"] == 1
        assert entry["vcpus"] == 7     # 8 - 1 headroom
        assert entry["memory_mib"] == 55_000  # 64 GiB - ~9 GiB for agent + OS

    def test_g6e_12xlarge_is_4_gpu(self) -> None:
        entry = INSTANCE_RESOURCES["g6e.12xlarge"]
        assert entry["gpus"] == 4
        assert entry["vcpus"] == 46

    def test_neuron_entries_have_zero_gpus(self) -> None:
        # inf2 / trn1 use Neuron, not CUDA GPUs
        assert INSTANCE_RESOURCES["inf2.24xlarge"]["gpus"] == 0
        assert INSTANCE_RESOURCES["trn1.32xlarge"]["gpus"] == 0

    def test_all_entries_have_required_keys(self) -> None:
        for instance_type, entry in INSTANCE_RESOURCES.items():
            assert set(entry) == {"vcpus", "memory_mib", "gpus"}, \
                f"{instance_type} has wrong keys"
            assert entry["vcpus"] >= 1
            assert entry["memory_mib"] >= 1_000
            assert entry["gpus"] >= 0


class TestResourceRequirementsHelper:
    def test_known_instance_type(self) -> None:
        reqs = _resource_requirements("g7e.2xlarge")
        by_type = {r["Type"]: r["Value"] for r in reqs}
        assert by_type == {"VCPU": "7", "MEMORY": "55000", "GPU": "1"}

    def test_unknown_instance_type_falls_back(self) -> None:
        reqs = _resource_requirements("future.instance")
        by_type = {r["Type"]: r["Value"] for r in reqs}
        # Conservative fallback: 4 vCPU, 16 GiB, 1 GPU
        assert by_type == {"VCPU": "4", "MEMORY": "16000", "GPU": "1"}


class TestJobDefinitionShape:
    """End-to-end: build_template for each instance type and verify the
    JobDefinition's ResourceRequirements come out right."""

    @pytest.mark.parametrize("instance_type,expected_gpus,expected_vcpus", [
        ("p4d.24xlarge", "8", "92"),
        ("p5.48xlarge", "8", "188"),
        ("g5.12xlarge", "4", "46"),
        ("g7e.2xlarge", "1", "7"),
    ])
    def test_resource_requirements_match_table(
        self, instance_type: str, expected_gpus: str, expected_vcpus: str,
    ) -> None:
        tpl = build_template(_plan(instance_type))
        jd = tpl["Resources"]["JobDefinition"]
        reqs = jd["Properties"]["ContainerProperties"]["ResourceRequirements"]
        by_type = {r["Type"]: r["Value"] for r in reqs}
        assert by_type["GPU"] == expected_gpus
        assert by_type["VCPU"] == expected_vcpus

    def test_g7e_2xlarge_memory_under_64gb(self) -> None:
        """Sanity: g7e.2xl has 64 GiB RAM. We must ask for less to leave
        room for the ECS agent + OS."""
        tpl = build_template(_plan("g7e.2xlarge"))
        reqs = tpl["Resources"]["JobDefinition"]["Properties"][
            "ContainerProperties"]["ResourceRequirements"]
        mem = int(next(r["Value"] for r in reqs if r["Type"] == "MEMORY"))
        assert mem < 64 * 1024  # 65536 MiB
        assert mem > 40 * 1024  # not silly-small
