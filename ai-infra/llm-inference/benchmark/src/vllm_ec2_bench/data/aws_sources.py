"""AWS API adapters for the hardware catalog.

These are pure functions — given a boto3 client, they call a specific AWS API
and return parsed Python values. They don't know about :class:`HardwareSpec`;
that's assembled one level up (see :meth:`HardwareSpec.from_aws`).

Two APIs covered:

1. ``ec2.describe_instance_types`` — vCPU, RAM, accelerator count + memory,
   accelerator model name.
2. ``pricing.get_products`` — on-demand Linux-shared hourly price in USD.

Capacity-Block pricing lives in ``DescribeCapacityBlockOfferings`` which is
demand-driven (an offering is a specific start date + fee). That's queried
at strategy time, not at catalog time.
"""
from __future__ import annotations

import json
import logging
from typing import Any

LOG = logging.getLogger(__name__)


# AWS doesn't expose GPU architecture ("Ampere", "Ada Lovelace", "Hopper", ...)
# directly — only the marketing GPU name. This map is a static lookup; add
# entries when adding new instance types. Longest keys are tried first so
# e.g. "Inferentia2" matches before "Inferentia".
_ARCH_FROM_ACCELERATOR_NAME: dict[str, str] = {
    # NVIDIA GPUs
    "A10G":                  "Ampere",
    "T4G":                   "Turing",
    "T4":                    "Turing",
    "V100":                  "Volta",
    "L40S":                  "Ada Lovelace",
    "L4":                    "Ada Lovelace",
    # Blackwell (g7e instances). AWS calls this "RTX PRO Server 6000" and
    # also "RTX PRO 6000 Blackwell" depending on region/time; accept both.
    "RTX PRO Server 6000":   "Blackwell",
    "RTX PRO 6000 Blackwell":"Blackwell",
    "B200":                  "Blackwell",
    "A100":                  "Ampere",          # covers A100 40GB + 80GB
    "H100":                  "Hopper",
    "H200":                  "Hopper (H200)",
    # AWS Neuron — order matters: *2 before the base.
    "Inferentia2":           "Neuron 2nd gen",
    "Inferentia":            "Neuron 1st gen",
    "Trainium3":             "Neuron 3rd gen",
    "Trainium2":             "Neuron 2nd gen",
    "Trainium":              "Neuron 1st gen",
}


def guess_architecture(accelerator_name: str) -> str:
    """Best-effort mapping from AWS-returned name to a generation label.

    Tries longest keys first so e.g. ``"Inferentia2"`` doesn't match the
    ``"Inferentia"`` entry.
    """
    # Sort by key length desc so specific matches beat generic ones.
    for key in sorted(_ARCH_FROM_ACCELERATOR_NAME, key=len, reverse=True):
        if key in accelerator_name:
            return _ARCH_FROM_ACCELERATOR_NAME[key]
    LOG.warning("Unknown accelerator name %r — architecture will be 'unknown'", accelerator_name)
    return "unknown"


def fetch_hardware_from_describe_instance_types(
    instance_type: str, ec2_client: Any,
) -> dict[str, Any]:
    """Call ``ec2:DescribeInstanceTypes`` and return the parsed hardware fields.

    Returns a dict with keys:
    ``family``, ``accelerator_model``, ``accelerator_architecture``,
    ``num_accelerators``, ``vram_gib_per_accelerator``, ``vcpu``, ``ram_gib``.

    Raises ``RuntimeError`` if the instance type is not returned by the API
    (e.g. not offered in the account, typo, or not yet launched).
    """
    resp = ec2_client.describe_instance_types(InstanceTypes=[instance_type])
    types = resp.get("InstanceTypes", [])
    if not types:
        raise RuntimeError(f"No InstanceTypes entry for {instance_type}")
    info = types[0]

    vcpu = int(info["VCpuInfo"]["DefaultVCpus"])
    ram_gib = int(round(info["MemoryInfo"]["SizeInMiB"] / 1024))

    # GPU vs Neuron
    if "GpuInfo" in info:
        gpus = info["GpuInfo"]["Gpus"]
        if len(gpus) != 1:
            # All supported instances are homogeneous; warn if not
            LOG.warning("%s has %d heterogeneous GPU slots", instance_type, len(gpus))
        gpu = gpus[0]
        accel_name_raw = gpu["Name"]
        manufacturer = gpu.get("Manufacturer", "")
        accel_model = f"{manufacturer} {accel_name_raw}".strip()
        num_accelerators = int(gpu["Count"])
        # MemoryInfo.SizeInMiB is per-GPU (confirmed by AWS docs)
        vram_gib_per_accelerator = float(gpu["MemoryInfo"]["SizeInMiB"]) / 1024
        family = "gpu"
    elif "NeuronInfo" in info:
        neuron = info["NeuronInfo"]
        devices = neuron["NeuronDevices"]
        if len(devices) != 1:
            LOG.warning("%s has %d heterogeneous Neuron devices", instance_type, len(devices))
        dev = devices[0]
        accel_name_raw = dev["Name"]                    # e.g. "Inferentia2"
        manufacturer = "AWS"
        accel_model = f"{manufacturer} {accel_name_raw}".strip()
        num_accelerators = int(dev["Count"])
        # Per-device memory: NeuronDevices[i].MemoryInfo.SizeInMiB
        vram_gib_per_accelerator = float(dev["MemoryInfo"]["SizeInMiB"]) / 1024
        family = "neuron"
    else:
        raise RuntimeError(
            f"{instance_type} has neither GpuInfo nor NeuronInfo — "
            "not a supported instance type for this catalog."
        )

    return {
        "family": family,
        "accelerator_model": accel_model,
        "accelerator_architecture": guess_architecture(accel_name_raw),
        "num_accelerators": num_accelerators,
        "vram_gib_per_accelerator": round(vram_gib_per_accelerator, 3),
        "vcpu": vcpu,
        "ram_gib": ram_gib,
    }


def fetch_on_demand_price(
    instance_type: str, region: str, pricing_client: Any,
) -> float | None:
    """Call ``pricing:GetProducts`` and return the Linux/Shared hourly USD price.

    Uses the 6-filter pattern that uniquely identifies one SKU per
    region × instance-type.

    Returns ``None`` if no SKU matches (can happen for p5e.48xlarge — CB-only).

    Note: the Pricing API returns multiple SKUs per (region, instance_type) —
    one OnDemand SKU plus zero-or-more CapacityBlock SKUs (the latter priced
    at $0 with the real fee paid at purchase time). We pick the first SKU
    whose ``marketoption`` is ``OnDemand``.
    """
    filters = [
        {"Type": "TERM_MATCH", "Field": "regionCode",       "Value": region},
        {"Type": "TERM_MATCH", "Field": "instanceType",     "Value": instance_type},
        {"Type": "TERM_MATCH", "Field": "operatingSystem",  "Value": "Linux"},
        {"Type": "TERM_MATCH", "Field": "tenancy",          "Value": "Shared"},
        {"Type": "TERM_MATCH", "Field": "preInstalledSw",   "Value": "NA"},
        {"Type": "TERM_MATCH", "Field": "capacitystatus",   "Value": "Used"},
    ]
    resp = pricing_client.get_products(
        ServiceCode="AmazonEC2", Filters=filters, MaxResults=10,
    )
    price_list = resp.get("PriceList", [])
    if not price_list:
        return None

    for raw in price_list:
        item = json.loads(raw)
        market = item.get("product", {}).get("attributes", {}).get("marketoption", "")
        if market != "OnDemand":
            continue
        terms = item.get("terms", {}).get("OnDemand", {})
        if not terms:
            continue
        term = next(iter(terms.values()))
        price_dim = next(iter(term["priceDimensions"].values()))
        price = float(price_dim["pricePerUnit"]["USD"])
        if price > 0:
            return price
    return None


__all__ = [
    "fetch_hardware_from_describe_instance_types",
    "fetch_on_demand_price",
    "guess_architecture",
]
