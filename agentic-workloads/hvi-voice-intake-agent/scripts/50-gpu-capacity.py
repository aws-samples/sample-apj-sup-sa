#!/usr/bin/env python3
"""Report SageMaker realtime-endpoint GPU quota in the data region.

The hvi-whisper endpoint failed with InsufficientInstanceCapacity on
ml.g5.xlarge. Before retrying we need to know which GPU families we are
actually entitled to, so we can hand SageMaker a prioritised list of
instance types instead of a single one.
"""
import json
import os
import re
import subprocess
import sys

REGION = os.environ.get("DATA_REGION", "ap-south-1")

# Families that can host whisper-large-v3 (needs >=16GB GPU RAM in fp16,
# comfortably ~10GB, so a single 24GB A10G or 16GB T4 both work).
FAMILY_RE = re.compile(r"ml\.(g4dn|g5|g6|g6e|p3|p4d|p5)\.")

# Preference order: cheapest adequate GPU first. whisper-large-v3 in fp16 is
# ~6GB of weights, so the smallest card in each family is fine and we would
# rather pay for xlarge than 12xlarge.
PREFERENCE = [
    "ml.g4dn.xlarge",
    "ml.g5.xlarge",
    "ml.g6.xlarge",
    "ml.g4dn.2xlarge",
    "ml.g5.2xlarge",
    "ml.g6.2xlarge",
    "ml.g4dn.4xlarge",
    "ml.g5.4xlarge",
    "ml.g6.4xlarge",
]


def main() -> int:
    # SageMaker exposes several hundred quotas, so this MUST paginate --
    # reading only the first page silently hides most GPU families.
    quotas = []
    token = None
    while True:
        cmd = [
            "aws", "service-quotas", "list-service-quotas",
            "--service-code", "sagemaker",
            "--region", REGION,
            "--max-results", "100",
            "--output", "json",
        ]
        if token:
            cmd += ["--next-token", token]
        out = subprocess.run(cmd, capture_output=True, text=True)
        if out.returncode != 0:
            print("failed to list quotas:", out.stderr.strip(), file=sys.stderr)
            return 1
        page = json.loads(out.stdout)
        quotas.extend(page.get("Quotas", []))
        token = page.get("NextToken")
        if not token:
            break

    # Quota names look like "ml.g5.xlarge for endpoint usage".
    allowed = {}
    for q in quotas:
        name = q["QuotaName"]
        if "endpoint usage" not in name:
            continue
        m = FAMILY_RE.search(name)
        if not m:
            continue
        itype = name.split(" for endpoint usage")[0].strip()
        allowed[itype] = q.get("Value") or 0.0

    print(f"region={REGION}  GPU endpoint quota:")
    for itype, val in sorted(allowed.items(), key=lambda kv: -kv[1]):
        if val > 0:
            print(f"  {val:5.1f}  {itype}")

    usable = [t for t in PREFERENCE if allowed.get(t, 0) >= 1]
    print()
    if not usable:
        print("NO_USABLE_GPU")
        return 2
    print("PRIORITY_LIST=" + ",".join(usable))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
