"""BatchDeploymentPlan — how the Batch infrastructure is shaped.

A ``BatchDeploymentPlan`` is the input to the deployer (creates CFN stack
with queues + compute envs + job definition). Captures: which model,
what hardware, what capacity mode, how many queues, staging conventions.
"""
from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .model import ModelSpec

# Capacity mode for the Compute Environment's instance provisioning.
# 'spot': SPOT compute environment.
# 'on-demand': EC2 on-demand.
# 'odcr': ON_DEMAND with a pre-existing Capacity Reservation target.
# 'capacity-block': ML Capacity Block (p5/p5e) pre-purchased.
CapacityMode = Literal["spot", "on-demand", "odcr", "capacity-block"]


class ComputeEnvironmentConfig(BaseModel):
    """One compute environment — one hardware pool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name_suffix: str = Field(..., min_length=1, max_length=20)
    """Unique suffix within the stack (e.g. 'gpu-spot', 'p4d')."""

    instance_types: list[str] = Field(..., min_length=1)
    """EC2 instance types the CE can draw from, e.g. ``["g7e.2xlarge"]``
    or ``["g7e.2xlarge", "g7e.12xlarge", "g7e.24xlarge", "g7e.48xlarge"]``
    for multi-pool spot coverage.

    For spot, provide a list that spans multiple pools to give
    ``SPOT_PRICE_CAPACITY_OPTIMIZED`` room to pick the cheapest available
    at launch time. AWS Batch packs one or more containers per host based
    on each job's GPU reservation — larger instance types auto-hold
    multiple containers (e.g., a 4-GPU host runs 4 single-GPU jobs).

    For topologically-heterogeneous fallback (e.g., g7e + g6e), the
    containers need different vLLM configurations, which requires
    separate JobDefinitions — not supported in this single-JobDef plan.
    """

    capacity_mode: CapacityMode = "spot"
    min_vcpus: int = Field(0, ge=0)
    max_vcpus: int = Field(96, ge=1)
    desired_vcpus: int = Field(0, ge=0)
    """Leave at 0 and let Batch scale based on queued jobs."""

    # Optional targets for capacity-reservation modes
    capacity_reservation_id: str | None = None
    """Required if capacity_mode == 'odcr' or 'capacity-block'."""

    subnet_ids: list[str] = Field(default_factory=list)
    """If empty, deployer picks default VPC subnets at deploy time."""

    security_group_ids: list[str] = Field(default_factory=list)
    """If empty, deployer creates one."""

    @field_validator("name_suffix")
    @classmethod
    def _check_suffix(cls, v: str) -> str:
        if not v.replace("-", "").isalnum() or not v[0].isalnum():
            raise ValueError(
                f"name_suffix {v!r} must be alphanumeric + hyphens."
            )
        return v

    @field_validator("instance_types")
    @classmethod
    def _check_instance_types(cls, v: list[str]) -> list[str]:
        bad = [t for t in v if not t or "." not in t]
        if bad:
            raise ValueError(
                f"Invalid EC2 instance types {bad!r}; must be strings like "
                f"'g7e.2xlarge'."
            )
        if len(set(v)) != len(v):
            raise ValueError(f"Duplicate instance types in {v!r}.")
        return v

    @model_validator(mode="after")
    def _check_capacity_targets(self) -> "ComputeEnvironmentConfig":
        needs_cr = self.capacity_mode in ("odcr", "capacity-block")
        if needs_cr and not self.capacity_reservation_id:
            raise ValueError(
                f"capacity_mode={self.capacity_mode!r} requires "
                "capacity_reservation_id to be set."
            )
        if self.min_vcpus > self.max_vcpus:
            raise ValueError("min_vcpus > max_vcpus")
        if self.desired_vcpus > self.max_vcpus:
            raise ValueError("desired_vcpus > max_vcpus")
        return self


class QueueConfig(BaseModel):
    """One Batch job queue."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name_suffix: str = Field(..., min_length=1, max_length=20)
    """Unique suffix within the stack, e.g. 'primary', 'fallback'."""

    priority: int = Field(1, ge=0, le=1000)
    compute_environment_suffixes: list[str] = Field(..., min_length=1)
    """Which ComputeEnvironmentConfig(s) feed this queue, by name_suffix."""

    @field_validator("name_suffix")
    @classmethod
    def _check_suffix(cls, v: str) -> str:
        if not v.replace("-", "").isalnum() or not v[0].isalnum():
            raise ValueError(f"name_suffix {v!r} must be alphanumeric + hyphens.")
        return v


class BatchDeploymentPlan(BaseModel):
    """Top-level plan passed to the deployer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_spec: ModelSpec
    region: str = "us-west-2"
    vpc_id: str | None = None
    """If None, deployer picks the default VPC at deploy time."""

    compute_environments: list[ComputeEnvironmentConfig] = Field(..., min_length=1)
    queues: list[QueueConfig] = Field(..., min_length=1)

    # Container runtime knobs (baked into the job definition)
    tensor_parallel: int = Field(1, ge=1)
    data_parallel: int = Field(1, ge=1)
    pipeline_parallel: int = Field(1, ge=1)
    max_model_len: int | None = None
    """Override ModelSpec.default_max_model_len. None = use default."""

    gpu_memory_utilization: float = Field(0.90, gt=0, le=0.98)
    in_flight_per_job: int = Field(32, ge=1, le=1024)
    """Default concurrency inside the container's asyncio driver."""

    enable_prefix_caching: bool = True
    """Pass ``--enable-prefix-caching`` to vLLM. For workloads with a
    repeated system prompt (common in batch jobs), this eliminates
    redundant prefill work and typically improves throughput 1.5-2x.
    Set to False only if your workload has no shared prefixes or if the
    vLLM backend doesn't support it (e.g. some Neuron builds)."""

    extra_serve_flags: str = ""
    """Optional extra flags appended to the ``vllm serve`` invocation in
    the container (e.g. ``--kv-cache-dtype fp8`` for Llama-4-Scout to fit
    KV cache on 8xA100-40G, or ``--quantization awq`` for an AWQ artifact).
    Empty string by default. Whitespace-trimmed before being passed to the
    runtime; never include shell metacharacters."""

    extra_env_vars: dict[str, str] = Field(default_factory=dict)
    """Optional model/plan-specific environment variables exported into the
    container before ``vllm serve`` runs (e.g.
    ``{"VLLM_USE_FLASHINFER_MOE_MXFP4_MXFP8": "1"}`` for gpt-oss-20b on
    Blackwell, or ``{"VLLM_ATTENTION_BACKEND": "TRITON_ATTN_VLLM_V1"}`` for
    gpt-oss-20b on Ampere). Names must match ``[A-Z_][A-Z0-9_]*``; values
    are passed verbatim. Reserved names (``HF_TOKEN``, ``HUGGING_FACE_HUB_TOKEN``,
    ``HF_MODEL_ID``, ``MODEL_ID``, ``TENSOR_PARALLEL_SIZE``, ``DATA_PARALLEL_SIZE``,
    ``PIPELINE_PARALLEL_SIZE``, ``MAX_MODEL_LEN``, ``GPU_MEMORY_UTILIZATION``,
    ``DTYPE``, ``IN_FLIGHT_PER_JOB``, ``ENABLE_PREFIX_CACHING``,
    ``EXTRA_SERVE_FLAGS``, ``VLLM_STARTUP_TIMEOUT_S``, ``REQUEST_TIMEOUT_S``,
    ``MANIFEST_S3_URI``, ``OUTPUT_PREFIX_S3_URI``, ``OVERWRITE``,
    ``AWS_REGION``) are forbidden so plans can't shadow the framework's own
    contract with the runtime."""

    # Timeouts (seconds)
    job_timeout_seconds: int = Field(24 * 60 * 60, ge=60)
    """Batch job attemptDurationSeconds (hard kill)."""

    vllm_startup_timeout_seconds: int = Field(900, ge=60)
    """How long the entrypoint waits for /v1/models to come up."""

    request_timeout_seconds: int = Field(120, ge=10, le=3600)
    """Per-request httpx timeout inside the driver's inference loop.

    The default 120s is fine for short generations (max_tokens<=512) on
    fast GPUs (H100/H200), but can clip on slower hardware (A10G/A100-40G)
    or larger ``max_tokens`` (>=2048). Decode is single-stream per request,
    and at ~30 tok/s on an A10G generating 2048 tokens, wall-clock crosses
    ~70s — within budget on its own, but at high ``in_flight_per_job``,
    requests queue behind each other and observed wall-clock per request
    can exceed 120s under load. Bump for slow-GPU + long-output configs.
    """

    root_volume_gib: int = Field(300, ge=100, le=2000)
    """Root EBS volume size for each Batch EC2 instance, in GiB. Must be
    large enough to hold the ECS-optimized AMI (~10 GiB), the vLLM
    container image (~24 GiB), AND the entire HuggingFace weight cache
    for the model. Llama-4-Scout-17B-16E is ~218 GiB at BF16, so the
    300 GiB default fits the lineup. Bump to ~400 GiB if you also want
    to keep an alternate-precision artifact (FP8/AWQ) on disk during
    testing. Drop to 150 GiB for single-GPU smaller models if you need
    the savings."""

    _RESERVED_ENV_NAMES: ClassVar[tuple[str, ...]] = (
        "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN",
        "HF_MODEL_ID", "MODEL_ID",
        "TENSOR_PARALLEL_SIZE", "DATA_PARALLEL_SIZE", "PIPELINE_PARALLEL_SIZE",
        "MAX_MODEL_LEN", "GPU_MEMORY_UTILIZATION", "DTYPE",
        "IN_FLIGHT_PER_JOB", "ENABLE_PREFIX_CACHING",
        "EXTRA_SERVE_FLAGS", "VLLM_STARTUP_TIMEOUT_S", "REQUEST_TIMEOUT_S",
        "MANIFEST_S3_URI", "OUTPUT_PREFIX_S3_URI", "OVERWRITE",
        "AWS_REGION",
    )

    @field_validator("extra_env_vars")
    @classmethod
    def _validate_extra_env_vars(cls, v: dict[str, str]) -> dict[str, str]:
        import re
        name_re = re.compile(r"^[A-Z_][A-Z0-9_]*$")
        for name in v:
            if not name_re.match(name):
                raise ValueError(
                    f"extra_env_vars name {name!r} must match [A-Z_][A-Z0-9_]*."
                )
            if name in cls._RESERVED_ENV_NAMES:
                raise ValueError(
                    f"extra_env_vars name {name!r} is reserved by the runtime "
                    "and cannot be overridden."
                )
        return v

    @model_validator(mode="after")
    def _check_topology(self) -> "BatchDeploymentPlan":
        # Queue references must resolve to a defined CE
        ce_names = {ce.name_suffix for ce in self.compute_environments}
        for q in self.queues:
            unknown = [c for c in q.compute_environment_suffixes if c not in ce_names]
            if unknown:
                raise ValueError(
                    f"Queue {q.name_suffix!r} references unknown compute "
                    f"environments: {unknown}. Defined: {sorted(ce_names)}"
                )
        # CE name_suffixes unique
        if len(ce_names) != len(self.compute_environments):
            raise ValueError("Duplicate compute_environment name_suffix.")
        q_names = {q.name_suffix for q in self.queues}
        if len(q_names) != len(self.queues):
            raise ValueError("Duplicate queue name_suffix.")
        # Parallelism sanity — vLLM requires TP * PP to divide the GPUs
        # evenly; we can't validate that without hardware info, so leave
        # it as a runtime check. Here we at least ensure integers make sense.
        return self

    # ------------------------------------------------------------------
    # Derived / convenience
    # ------------------------------------------------------------------
    @property
    def effective_max_model_len(self) -> int:
        return self.max_model_len or self.model_spec.default_max_model_len

    def compute_environment_name(
        self, stack_name: str, ce: ComputeEnvironmentConfig,
    ) -> str:
        """Fully-qualified CE name (CloudFormation logical ID basis)."""
        return f"{stack_name}-{ce.name_suffix}"

    def queue_name(self, stack_name: str, q: QueueConfig) -> str:
        return f"{stack_name}-{q.name_suffix}-queue"
