#!/usr/bin/env python
"""Report SageMaker endpoint-usage quota for GPU instance types, and what is using it.

Written because a create-endpoint failed on a quota that had nothing to do with
the instance the endpoint would actually run on. SageMaker validates EVERY entry
in an InstancePools ladder against quota up front, so one exhausted type in the
list fails the whole call - even at priority 3 that would never be reached. The
InstancePools ladder that protects against capacity gaps therefore has to be
built from types that have quota HEADROOM, not just from types that are adequate.

Run:  ./.venv/bin/python scripts/51-gpu-quota.py
"""
from __future__ import annotations

import os

import boto3

REGION = os.environ.get("DATA_REGION", "ap-south-1")

# The types worth considering for a ~3-7GB fp16 checkpoint at conversational
# latency. Anything smaller than 16GB VRAM is a false economy here.
CANDIDATES = [
    "ml.g4dn.xlarge", "ml.g4dn.2xlarge",
    "ml.g5.xlarge", "ml.g5.2xlarge",
    "ml.g6.xlarge", "ml.g6.2xlarge",
]


def main() -> int:
    sq = boto3.client("service-quotas", region_name=REGION)
    sm = boto3.client("sagemaker", region_name=REGION)

    # Who is consuming what. describe-endpoint reports CurrentInstanceType as null
    # when the variant was created with InstancePools, so the type has to come
    # from the endpoint config's resolved pools or the variant summary.
    # QUOTA IS RESERVED ACROSS THE WHOLE LADDER, NOT JUST THE RUNNING TYPE.
    #
    # This is the part that is easy to get wrong and produced a confusing
    # failure: hvi-whisper RUNS on ml.g4dn.xlarge, yet create-endpoint for a
    # second endpoint was rejected on 'ml.g6.xlarge for endpoint usage ... current
    # utilization of 1'. Because the variant may fall back to any pool at any
    # time, SageMaker holds a unit of quota against EVERY type in the ladder. So
    # a five-entry ladder consumes one unit from five different quotas, and any
    # type whose limit is 1 can appear in exactly one endpoint's ladder.
    running: dict[str, list[str]] = {}
    reserved: dict[str, list[str]] = {}
    for ep in sm.list_endpoints()["Endpoints"]:
        name = ep["EndpointName"]
        d = sm.describe_endpoint(EndpointName=name)
        for v in d.get("ProductionVariants", []):
            pools = v.get("InstancePools") or []
            if pools:
                for p in pools:
                    t = p["InstanceType"]
                    reserved.setdefault(t, []).append(name)
                    if (p.get("CurrentInstanceCount") or 0) > 0:
                        running.setdefault(t, []).append(name)
                # The config holds the full ladder; describe only reports the
                # pools that have been resolved, so read the rest from there.
                cfg = sm.describe_endpoint_config(
                    EndpointConfigName=d["EndpointConfigName"])
                for cv in cfg.get("ProductionVariants", []):
                    for p in (cv.get("InstancePools") or []):
                        t = p["InstanceType"]
                        if name not in reserved.setdefault(t, []):
                            reserved[t].append(name)
            else:
                t = v.get("CurrentInstanceType") or "unknown"
                running.setdefault(t, []).append(name)
                reserved.setdefault(t, []).append(name)

    print(f"region: {REGION}\n")
    print("actually running on:")
    for t, names in sorted(running.items()):
        print(f"  {t:24s} {', '.join(names)}")
    print("\nquota reserved by a ladder (counts even when not running there):")
    for t, names in sorted(reserved.items()):
        print(f"  {t:24s} {', '.join(sorted(set(names)))}")
    in_use = reserved

    print("\nendpoint-usage quota by instance type:")
    paginator = sq.get_paginator("list_service_quotas")
    quotas: dict[str, float] = {}
    for page in paginator.paginate(ServiceCode="sagemaker"):
        for q in page["Quotas"]:
            nm = q["QuotaName"]
            if nm.endswith("for endpoint usage"):
                itype = nm.replace(" for endpoint usage", "").strip()
                quotas[itype] = q["Value"]

    usable: list[str] = []
    for c in CANDIDATES:
        v = quotas.get(c)
        used = len(in_use.get(c, []))
        if v is None:
            print(f"  {c:24s} quota unknown")
            continue
        head = v - used
        flag = "OK" if head >= 1 else "EXHAUSTED"
        print(f"  {c:24s} quota {v:>4.0f}  in use {used}  headroom {head:>4.0f}  {flag}")
        if head >= 1:
            usable.append(c)

    print("\nladder safe to request now (max 5 entries, all must have headroom):")
    print("  " + ",".join(usable[:5]) if usable else "  NONE - request a quota increase")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
