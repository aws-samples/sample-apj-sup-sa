"""Tests for vllm_ec2_bench.data.aws_sources and HardwareSpec.from_aws.

We mock the boto3 client responses directly — lets us pin the exact parsing
behavior without needing AWS creds or moto coverage for Pricing.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from vllm_ec2_bench.data.aws_sources import (
    _ARCH_FROM_ACCELERATOR_NAME,
    fetch_hardware_from_describe_instance_types,
    fetch_on_demand_price,
    guess_architecture,
)
from vllm_ec2_bench.data.hardware_facts import HardwareFacts


# -----------------------------------------------------------------------------
# guess_architecture — longest-prefix-first matching
# -----------------------------------------------------------------------------
class TestGuessArchitecture:
    def test_nvidia_a10g(self) -> None:
        assert guess_architecture("NVIDIA A10G") == "Ampere"

    def test_ada_l40s(self) -> None:
        assert guess_architecture("NVIDIA L40S") == "Ada Lovelace"

    def test_hopper_h100(self) -> None:
        assert guess_architecture("NVIDIA H100") == "Hopper"

    def test_h200_separate_key(self) -> None:
        assert guess_architecture("NVIDIA H200") == "Hopper (H200)"

    def test_blackwell_short_name(self) -> None:
        assert guess_architecture("NVIDIA RTX PRO Server 6000") == "Blackwell"

    def test_blackwell_long_name(self) -> None:
        assert guess_architecture("NVIDIA RTX PRO 6000 Blackwell") == "Blackwell"

    def test_inferentia2_beats_inferentia(self) -> None:
        # The string 'Inferentia2' also contains 'Inferentia'; we must pick
        # the longer, more specific match.
        assert guess_architecture("Inferentia2") == "Neuron 2nd gen"

    def test_trainium2_beats_trainium(self) -> None:
        assert guess_architecture("Trainium2") == "Neuron 2nd gen"

    def test_trainium_plain(self) -> None:
        assert guess_architecture("Trainium") == "Neuron 1st gen"

    def test_unknown_name(self) -> None:
        assert guess_architecture("NVIDIA UnknownGPU Z9999") == "unknown"


# -----------------------------------------------------------------------------
# fetch_hardware_from_describe_instance_types
# -----------------------------------------------------------------------------
def _gpu_dit_response(
    *,
    instance_type: str,
    vcpu: int,
    ram_mib: int,
    gpu_name: str,
    gpu_count: int,
    gpu_mem_mib: int,
    manufacturer: str = "NVIDIA",
) -> dict:
    return {
        "InstanceTypes": [{
            "InstanceType": instance_type,
            "VCpuInfo": {"DefaultVCpus": vcpu},
            "MemoryInfo": {"SizeInMiB": ram_mib},
            "GpuInfo": {
                "Gpus": [{
                    "Name": gpu_name,
                    "Manufacturer": manufacturer,
                    "Count": gpu_count,
                    "MemoryInfo": {"SizeInMiB": gpu_mem_mib},
                }],
                "TotalGpuMemoryInMiB": gpu_count * gpu_mem_mib,
            },
        }]
    }


def _neuron_dit_response(
    *,
    instance_type: str,
    vcpu: int,
    ram_mib: int,
    device_name: str,
    device_count: int,
    device_mem_mib: int,
) -> dict:
    return {
        "InstanceTypes": [{
            "InstanceType": instance_type,
            "VCpuInfo": {"DefaultVCpus": vcpu},
            "MemoryInfo": {"SizeInMiB": ram_mib},
            "NeuronInfo": {
                "NeuronDevices": [{
                    "Name": device_name,
                    "Count": device_count,
                    "CoreInfo": {"Count": 2, "Version": 2},
                    "MemoryInfo": {"SizeInMiB": device_mem_mib},
                }],
                "TotalNeuronDeviceMemoryInMiB": device_count * device_mem_mib,
            },
        }]
    }


class TestFetchHardware:
    def test_gpu_happy_path(self) -> None:
        ec2 = MagicMock()
        ec2.describe_instance_types.return_value = _gpu_dit_response(
            instance_type="g5.12xlarge",
            vcpu=48, ram_mib=196608,          # 192 GiB
            gpu_name="A10G", gpu_count=4,
            gpu_mem_mib=22888,                # ≈ 22.35 GiB
        )
        hw = fetch_hardware_from_describe_instance_types("g5.12xlarge", ec2)
        assert hw["family"] == "gpu"
        assert hw["vcpu"] == 48
        assert hw["ram_gib"] == 192
        assert hw["num_accelerators"] == 4
        assert hw["accelerator_model"] == "NVIDIA A10G"
        assert hw["accelerator_architecture"] == "Ampere"
        assert abs(hw["vram_gib_per_accelerator"] - 22.35) < 0.01

    def test_neuron_happy_path(self) -> None:
        ec2 = MagicMock()
        ec2.describe_instance_types.return_value = _neuron_dit_response(
            instance_type="inf2.24xlarge",
            vcpu=96, ram_mib=393216,          # 384 GiB
            device_name="Inferentia2", device_count=6,
            device_mem_mib=32768,             # 32 GiB/device
        )
        hw = fetch_hardware_from_describe_instance_types("inf2.24xlarge", ec2)
        assert hw["family"] == "neuron"
        assert hw["accelerator_model"] == "AWS Inferentia2"
        assert hw["accelerator_architecture"] == "Neuron 2nd gen"
        assert hw["num_accelerators"] == 6
        assert hw["vram_gib_per_accelerator"] == 32.0

    def test_unknown_instance_type_raises(self) -> None:
        ec2 = MagicMock()
        ec2.describe_instance_types.return_value = {"InstanceTypes": []}
        with pytest.raises(RuntimeError, match="No InstanceTypes entry"):
            fetch_hardware_from_describe_instance_types("fake.99xlarge", ec2)

    def test_cpu_only_instance_rejected(self) -> None:
        """An instance with neither GpuInfo nor NeuronInfo isn't supported."""
        ec2 = MagicMock()
        ec2.describe_instance_types.return_value = {
            "InstanceTypes": [{
                "InstanceType": "m7i.4xlarge",
                "VCpuInfo": {"DefaultVCpus": 16},
                "MemoryInfo": {"SizeInMiB": 65536},
            }]
        }
        with pytest.raises(RuntimeError, match="neither GpuInfo nor NeuronInfo"):
            fetch_hardware_from_describe_instance_types("m7i.4xlarge", ec2)


# -----------------------------------------------------------------------------
# fetch_on_demand_price
# -----------------------------------------------------------------------------
def _pricing_sku(
    *,
    sku: str,
    market: str,            # "OnDemand" | "CapacityBlock"
    usd_per_hour: float,
    tenancy: str = "Shared",
) -> str:
    """Return a stringified-JSON SKU as the real Pricing API does."""
    return json.dumps({
        "product": {
            "sku": sku,
            "attributes": {
                "marketoption": market,
                "tenancy": tenancy,
                "capacitystatus": "Used",
            },
        },
        "terms": {
            "OnDemand": {
                f"{sku}.TERM": {
                    "priceDimensions": {
                        f"{sku}.DIM": {
                            "pricePerUnit": {"USD": f"{usd_per_hour:.10f}"},
                            "unit": "Hrs",
                        },
                    },
                },
            },
        },
    })


class TestFetchOnDemandPrice:
    def test_happy_path_single_sku(self) -> None:
        pricing = MagicMock()
        pricing.get_products.return_value = {
            "PriceList": [_pricing_sku(sku="ABC", market="OnDemand", usd_per_hour=5.672)]
        }
        assert fetch_on_demand_price("g5.12xlarge", "us-east-2", pricing) == 5.672

    def test_skips_zero_dollar_capacity_block_sku(self) -> None:
        """CB SKUs are returned alongside OD with price $0 — must skip them."""
        pricing = MagicMock()
        pricing.get_products.return_value = {
            "PriceList": [
                _pricing_sku(sku="CB", market="CapacityBlock", usd_per_hour=0.0),
                _pricing_sku(sku="OD", market="OnDemand", usd_per_hour=21.9576),
            ]
        }
        assert fetch_on_demand_price("p4d.24xlarge", "us-east-2", pricing) == 21.9576

    def test_returns_none_when_no_skus(self) -> None:
        """p5e.48xlarge has no OD pricing in any region."""
        pricing = MagicMock()
        pricing.get_products.return_value = {"PriceList": []}
        assert fetch_on_demand_price("p5e.48xlarge", "us-east-2", pricing) is None

    def test_returns_none_when_only_zero_dollar_skus(self) -> None:
        pricing = MagicMock()
        pricing.get_products.return_value = {
            "PriceList": [_pricing_sku(sku="CB", market="CapacityBlock", usd_per_hour=0.0)]
        }
        assert fetch_on_demand_price("p5e.48xlarge", "us-east-2", pricing) is None

    def test_applies_six_filters(self) -> None:
        """The 6-filter pattern is required to get a clean result."""
        pricing = MagicMock()
        pricing.get_products.return_value = {"PriceList": []}
        fetch_on_demand_price("g5.12xlarge", "us-east-2", pricing)
        call_filters = pricing.get_products.call_args.kwargs["Filters"]
        fields = {f["Field"] for f in call_filters}
        assert fields == {
            "regionCode", "instanceType", "operatingSystem",
            "tenancy", "preInstalledSw", "capacitystatus",
        }


# -----------------------------------------------------------------------------
# HardwareFacts.from_describe_instance_types integration (still mocked)
# -----------------------------------------------------------------------------
class TestHardwareFactsFromAws:
    def test_assembles_facts_from_describe(self) -> None:
        ec2 = MagicMock()
        ec2.describe_instance_types.return_value = _gpu_dit_response(
            instance_type="g5.12xlarge",
            vcpu=48, ram_mib=196608, gpu_name="A10G",
            gpu_count=4, gpu_mem_mib=22888,
        )
        facts = HardwareFacts.from_describe_instance_types("g5.12xlarge", ec2)
        assert facts.instance_type == "g5.12xlarge"
        assert facts.num_accelerators == 4
        assert facts.accelerator_architecture == "Ampere"
        assert facts.family == "gpu"

    def test_cb_only_instance_fetch_hardware_only(self) -> None:
        """HardwareFacts doesn't carry prices — OD absence is irrelevant here."""
        ec2 = MagicMock()
        ec2.describe_instance_types.return_value = _gpu_dit_response(
            instance_type="p5e.48xlarge",
            vcpu=192, ram_mib=2097152, gpu_name="H200",
            gpu_count=8, gpu_mem_mib=144384,
        )
        facts = HardwareFacts.from_describe_instance_types("p5e.48xlarge", ec2)
        assert facts.num_accelerators == 8
        assert facts.vram_gib_per_accelerator == pytest.approx(141.0, abs=0.1)
