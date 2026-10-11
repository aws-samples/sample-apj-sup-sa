"""Per-endpoint GPU-preflight custom-resource handler (Inference stack).

Reproduces the **boto3 quota path** of ``scripts/51-gpu-quota.py`` — the
service-quotas ``list_service_quotas`` paginator (``"… for endpoint usage"``)
plus ``list_endpoints`` / ``describe_endpoint`` / ``describe_endpoint_config``
to compute the reserved-vs-running split and the live headroom per instance
type. It is **not** the ``subprocess``/``aws`` CLI form of
``50-gpu-capacity.py`` (a Lambda runtime has no ``aws`` CLI).

The lesson the scripts encode (and this preserves): SageMaker reserves quota
against **every** pool in an ``InstancePools`` ladder, not just the one a
variant runs on, so one exhausted type fails the whole ``create-endpoint`` even
at a priority that would never be reached. This handler takes **one** endpoint's
fixed candidate ladder and returns the **same ladder filtered to entries with
>=1 live headroom**, preserving membership order and the per-endpoint cap. It
**never adds** a pool not in that ladder and **never reorders**; it only drops
headroom-less entries. If the filtered ladder is empty it raises
``NO_USABLE_GPU``, failing the stack deployment and rolling back — rather than
creating an endpoint that cannot schedule and hangs ~30 minutes.

Invoked by the CDK ``Provider`` framework; the event carries ``RequestType``
and ``ResourceProperties`` (``Region``, ``EndpointName``, ``CandidateLadder`` —
a comma-separated ordered ladder — and ``Cap``). The usable ladder is returned
as the ``Ladder`` response attribute (comma-separated), consumed by the
endpoint custom resource via ``Fn::GetAtt``.
"""

from __future__ import annotations

import os

import boto3

_DEFAULT_REGION = os.environ.get("DATA_REGION", "ap-south-1")


def _physical_id(event: dict) -> str:
    name = (event.get("ResourceProperties") or {}).get("EndpointName", "endpoint")
    return f"hvi-gpu-preflight-{name}"


def _reserved_by_ladders(sm) -> dict:  # noqa: ANN001
    """Map instance-type -> endpoints reserving it (whole ladder, per 51).

    QUOTA IS RESERVED ACROSS THE WHOLE LADDER, NOT JUST THE RUNNING TYPE — a
    variant may fall back to any pool at any time, so SageMaker holds a unit of
    quota against every type named in the ladder. ``describe_endpoint`` only
    reports resolved pools, so the full ladder is read back from the endpoint
    config, exactly as ``scripts/51-gpu-quota.py`` does.
    """
    reserved: dict[str, list[str]] = {}
    for ep in sm.list_endpoints()["Endpoints"]:
        name = ep["EndpointName"]
        described = sm.describe_endpoint(EndpointName=name)
        for variant in described.get("ProductionVariants", []):
            pools = variant.get("InstancePools") or []
            if pools:
                for pool in pools:
                    reserved.setdefault(pool["InstanceType"], [])
                    if name not in reserved[pool["InstanceType"]]:
                        reserved[pool["InstanceType"]].append(name)
                config = sm.describe_endpoint_config(
                    EndpointConfigName=described["EndpointConfigName"]
                )
                for cfg_variant in config.get("ProductionVariants", []):
                    for pool in cfg_variant.get("InstancePools") or []:
                        reserved.setdefault(pool["InstanceType"], [])
                        if name not in reserved[pool["InstanceType"]]:
                            reserved[pool["InstanceType"]].append(name)
            else:
                itype = variant.get("CurrentInstanceType") or "unknown"
                reserved.setdefault(itype, [])
                if name not in reserved[itype]:
                    reserved[itype].append(name)
    return reserved


def _endpoint_usage_quotas(sq) -> dict:  # noqa: ANN001
    """Instance-type -> endpoint-usage quota value (the "… for endpoint usage")."""
    quotas: dict[str, float] = {}
    paginator = sq.get_paginator("list_service_quotas")
    for page in paginator.paginate(ServiceCode="sagemaker"):
        for quota in page["Quotas"]:
            name = quota["QuotaName"]
            if name.endswith("for endpoint usage"):
                itype = name.replace(" for endpoint usage", "").strip()
                quotas[itype] = quota["Value"]
    return quotas


def _usable_ladder(
    candidates: list[str], cap: int, reserved: dict, quotas: dict
) -> list[str]:
    """Filter ``candidates`` to entries with >=1 headroom; keep order + cap."""
    usable: list[str] = []
    for candidate in candidates:
        quota = quotas.get(candidate)
        if quota is None:
            # Unknown quota -> cannot prove headroom -> drop (fail-safe), exactly
            # as 51 treats an unknown quota as non-usable.
            continue
        used = len(reserved.get(candidate, []))
        if (quota - used) >= 1:
            usable.append(candidate)
    return usable[:cap]


def handler(event: dict, _context) -> dict:  # noqa: ANN001
    request_type = event.get("RequestType")
    if request_type == "Delete":
        # The preflight owns no AWS resource; nothing to undo.
        return {"PhysicalResourceId": _physical_id(event)}

    props = event.get("ResourceProperties", {})
    region = props.get("Region") or _DEFAULT_REGION
    candidates = [c for c in (props.get("CandidateLadder") or "").split(",") if c]
    if not candidates:
        raise RuntimeError("gpu preflight received an empty candidate ladder")
    # Cap arrives as a string over the CloudFormation custom-resource boundary.
    cap = int(props.get("Cap") or len(candidates))

    sq = boto3.client("service-quotas", region_name=region)
    sm = boto3.client("sagemaker", region_name=region)

    reserved = _reserved_by_ladders(sm)
    quotas = _endpoint_usage_quotas(sq)
    usable = _usable_ladder(candidates, cap, reserved, quotas)

    if not usable:
        raise RuntimeError(
            "NO_USABLE_GPU: none of "
            f"{candidates} has SageMaker endpoint-usage quota headroom in "
            f"{region}. Request a quota increase (see scripts/51-gpu-quota.py) "
            "before deploying rather than creating an endpoint that cannot "
            "schedule."
        )

    return {
        "PhysicalResourceId": _physical_id(event),
        "Data": {"Ladder": ",".join(usable)},
    }
