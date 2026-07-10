"""Predefined BatchDeploymentPlan variants for MedGemma-27B.

Users can pick one, or build their own. Each plan describes:
- hardware (list of instance types + parallelism)
- capacity mode (spot/on-demand/...)
- queue topology (usually single queue, optionally multi-queue with
  different CE priorities for failover)
"""
from llm_batch_deploy import (
    BatchDeploymentPlan,
    ComputeEnvironmentConfig,
    QueueConfig,
)

from .model_spec import MEDGEMMA_27B


# g7e Blackwell spot instance types that can all satisfy one JobDef
# (TP=1, gpus=1). Three 1-GPU sizes (2/4/8xlarge) + one 2-GPU size
# (12xlarge). Total = 4 distinct spot pools per AZ, so across 3 AZs
# that's 12 pools for Batch's SPOT_PRICE_CAPACITY_OPTIMIZED to pick
# from.
#
# Sizing tradeoffs inside the list:
#   - g7e.2xlarge  (1 GPU,  8 vCPU,  64 GiB) - the sweet spot on $/container
#   - g7e.4xlarge  (1 GPU, 16 vCPU, 128 GiB) - same 1 GPU, more host RAM
#   - g7e.8xlarge  (1 GPU, 32 vCPU, 256 GiB) - same 1 GPU, even more RAM
#   - g7e.12xlarge (2 GPUs, 48 vCPU, 512 GiB) - Batch packs 2 containers
#
# The 4xl/8xl sizes cost more per container than 2xl (you pay for
# vCPUs + RAM you can't use with a gpus=1 JobDef), so Batch only
# actually picks them when cheaper pools are unavailable. Their value
# is being there as a fallback when the 2xl pool is tight. The 12xl
# is efficient (2 containers per host = same $/container as 2xl).
#
# Larger sizes (24xlarge, 48xlarge) are intentionally excluded: their
# spot availability tends to be thin, and if Batch provisions one
# speculatively you pay for a 4- or 8-GPU host while only a few
# containers are actually running.
_G7E_BLACKWELL_MULTIPOOL = [
    "g7e.2xlarge",    # 1 GPU,  8 vCPU,  64 GiB - 1 container/host (ideal)
    "g7e.4xlarge",    # 1 GPU, 16 vCPU, 128 GiB - 1 container/host (fallback)
    "g7e.8xlarge",    # 1 GPU, 32 vCPU, 256 GiB - 1 container/host (fallback)
    "g7e.12xlarge",   # 2 GPUs, 48 vCPU, 512 GiB - 2 containers/host (ideal)
]


def g7e_spot_single_queue(
    instance_types: list[str] | None = None,
) -> BatchDeploymentPlan:
    """Default plan: g7e Blackwell multi-pool spot, one queue.

    Parameters
    ----------
    instance_types
        Optional override. Defaults to the 4-type Blackwell multi-pool
        ``[g7e.2xlarge, g7e.4xlarge, g7e.8xlarge, g7e.12xlarge]``. Pass
        a subset (e.g. ``["g7e.2xlarge"]``) to pin the CE to a single
        instance type — useful for controlled benchmarks where the
        cheapest pick across a multi-pool would vary run-to-run.

    Hardware: 1× RTX PRO 6000 Blackwell per container (96 GiB VRAM).
    Parallelism: TP=1, DP=1, PP=1 — one MedGemma-27B replica per container.
    Instance pool: g7e.2xlarge / 4xlarge / 8xlarge / 12xlarge. Batch
    picks whichever has the best current spot price + capacity via
    SPOT_PRICE_CAPACITY_OPTIMIZED. GPU pinning lets Batch pack 2
    containers onto a g7e.12xlarge (2 GPUs). The 1-GPU 4xl/8xl sizes
    exist as fallback pools — Batch only picks them when the cheaper
    2xl/12xl pools are short on capacity.

    Why this plan
    -------------
    * Blackwell's FP4/FP8 kernels + 96 GiB VRAM fit MedGemma-27B (54 GiB
      bf16 weights) on one GPU comfortably with generous KV budget.
    * Multi-pool coverage across 4 instance types × 3 AZs = 12 effective
      spot pools → better availability + lower interruption rate than
      a single-type CE.
    * All 4 sizes share the same GPU topology (1 GPU, 96 GiB VRAM) so
      a single JobDef (``gpus=1, TP=1``) fits all hosts.

    Concurrency + parallelism
    -------------------------
    Default ``in_flight_per_job=100`` is enough to saturate one Blackwell
    for short-prompt workloads (at bf16: 54 GiB weights leaves ~42 GiB
    for KV cache, covering 100 × 16k sequences easily). Per-container
    aggregate throughput is typically 1,000-1,400 output tok/s.

    Scaling knob: ``max_vcpus`` caps total vCPUs across all hosts Batch
    will provision. Default 80 vCPU = up to 10 × g7e.2xlarge OR 1 ×
    g7e.12xlarge (+ some headroom on smaller sizes). Batch's allocation
    picks the blend that best fulfills the current job queue at the
    cheapest spot price.
    """
    return BatchDeploymentPlan(
        model_spec=MEDGEMMA_27B,
        region="us-west-2",
        compute_environments=[
            ComputeEnvironmentConfig(
                name_suffix="g7e-spot",
                instance_types=instance_types or _G7E_BLACKWELL_MULTIPOOL,
                capacity_mode="spot",
                min_vcpus=0,
                max_vcpus=80,
                desired_vcpus=0,
            ),
        ],
        queues=[
            QueueConfig(
                name_suffix="primary",
                priority=1,
                compute_environment_suffixes=["g7e-spot"],
            ),
        ],
        tensor_parallel=1,
        data_parallel=1,
        pipeline_parallel=1,
        in_flight_per_job=100,
        max_model_len=16384,
    )


def g7e_family_spot_with_od_failover() -> BatchDeploymentPlan:
    """Spot-first with on-demand fallback, all within the g7e family.

    Use this when running the same workload but you want insurance
    against an unusually long spot capacity crunch. Jobs land on the
    spot CE first (much cheaper, ~$0.58/hr/container typical). If spot
    is unavailable for all g7e Blackwell pools simultaneously (rare but
    possible during regional crunches), Batch falls through to the
    on-demand CE at ~$3.36/hr/container.

    Both CEs use the same JobDef (gpus=1, TP=1) because g7e.* all share
    the same 1-GPU Blackwell 96-GiB topology. Customer-side UX stays
    unchanged: single submit, single queue, single job definition.

    Cost trade-off: on-demand is ~5.8× spot price. Treat the OD CE as
    insurance for urgent jobs; if wall-clock flexibility is OK, prefer
    ``g7e_spot_single_queue`` and let Batch wait for spot capacity.
    """
    return BatchDeploymentPlan(
        model_spec=MEDGEMMA_27B,
        region="us-west-2",
        compute_environments=[
            ComputeEnvironmentConfig(
                name_suffix="g7e-spot",
                instance_types=_G7E_BLACKWELL_MULTIPOOL,
                capacity_mode="spot",
                max_vcpus=80,
            ),
            ComputeEnvironmentConfig(
                name_suffix="g7e-ondemand",
                # For on-demand we stick to the smallest size — it's the
                # cheapest way to provide insurance, and 1-container-per-host
                # is fine when you're only running OD as a last resort.
                instance_types=["g7e.2xlarge"],
                capacity_mode="on-demand",
                max_vcpus=40,   # smaller OD cap keeps worst-case cost bounded
            ),
        ],
        queues=[
            QueueConfig(
                name_suffix="primary",
                priority=1,
                # Batch tries CEs in list order; spot wins when available.
                compute_environment_suffixes=["g7e-spot", "g7e-ondemand"],
            ),
        ],
        tensor_parallel=1,
        data_parallel=1,
        pipeline_parallel=1,
        in_flight_per_job=100,
        max_model_len=16384,
    )


def p4d_spot_single_queue() -> BatchDeploymentPlan:
    """Single-queue plan on p4d.24xlarge spot.

    Older-generation (A100-40GB) fallback. Benchmark data shows this is
    ~19× more expensive per output token than g7e spot for MedGemma-27B,
    so prefer ``g7e_spot_single_queue`` unless g7e is genuinely unavailable
    in your region. Kept for completeness and for users who want the
    larger VRAM pool (320 GiB total across 8 A100-40GB).
    """
    return BatchDeploymentPlan(
        model_spec=MEDGEMMA_27B,
        region="us-west-2",
        compute_environments=[
            ComputeEnvironmentConfig(
                name_suffix="p4d-spot",
                instance_types=["p4d.24xlarge"],
                capacity_mode="spot",
                min_vcpus=0,
                max_vcpus=96,
                desired_vcpus=0,
            ),
        ],
        queues=[
            QueueConfig(
                name_suffix="primary",
                priority=1,
                compute_environment_suffixes=["p4d-spot"],
            ),
        ],
        tensor_parallel=2,
        data_parallel=4,
        pipeline_parallel=1,
        in_flight_per_job=32,
    )


def p4d_spot_and_on_demand_failover() -> BatchDeploymentPlan:
    """p4d spot-first with on-demand fallback (same JobDef, same family)."""
    return BatchDeploymentPlan(
        model_spec=MEDGEMMA_27B,
        region="us-west-2",
        compute_environments=[
            ComputeEnvironmentConfig(
                name_suffix="p4d-spot",
                instance_types=["p4d.24xlarge"],
                capacity_mode="spot",
                max_vcpus=96,
            ),
            ComputeEnvironmentConfig(
                name_suffix="p4d-ondemand",
                instance_types=["p4d.24xlarge"],
                capacity_mode="on-demand",
                max_vcpus=96,
            ),
        ],
        queues=[
            QueueConfig(
                name_suffix="primary",
                priority=1,
                compute_environment_suffixes=["p4d-spot", "p4d-ondemand"],
            ),
        ],
        tensor_parallel=2,
        data_parallel=4,
        in_flight_per_job=32,
    )
