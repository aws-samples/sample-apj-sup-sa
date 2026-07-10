# Building Robotics Foundation Models on Amazon EKS

AWS sample implementation for deploying NVIDIA OSMO and validating robotics foundation model workflows on Amazon EKS.

This sample owns the AWS side of the stack: a secure EKS landing zone, GPU capacity management, AWS managed backing services, OSMO deployment wrappers, workflow examples, representative artifacts, and compatibility notes. NVIDIA OSMO remains an external pinned dependency; this repo intentionally does not vendor NVIDIA OSMO source, NVIDIA Terraform, or local OSMO patches.

## What This Provides

### AWS Infrastructure

- Current standard-support Amazon EKS baseline on private subnets.
- EKS API access remains private-capable; the Quick Start deploy wrapper opens
  the public API endpoint only to the caller's current IPv4 `/32` so follow-up
  `kubectl` and Helm deployment commands can run outside the VPC.
- AWS-native backing services for OSMO: Amazon RDS PostgreSQL, Amazon ElastiCache for Redis, Amazon S3, Amazon ECR, AWS KMS, and IRSA.
- Karpenter GPU NodePools for On-Demand G7e and G6e instances with private subnet placement, IMDSv2, encrypted gp3 root volumes, and a pinned EKS AL2023 NVIDIA AMI.
- NVIDIA GPU Operator installed from a pinned Helm chart with driver/toolkit installation disabled for the EKS NVIDIA AMI.
- AWS EFA device plugin installed from a pinned Helm chart with the GPU taint toleration required to expose `vpc.amazonaws.com/efa` on EFA-capable GPU nodes.

### OSMO Deployment

- KAI Scheduler installed from a pinned OCI Helm chart so OSMO workflows use the real `scheduling.run.ai` PodGroup CRDs and `kai-scheduler`.
- Deployment scripts that install OSMO with explicit Helm values instead of invoking upstream `deploy-k8s.sh`.
- OSMO Web UI installed as a private ClusterIP service for local `kubectl port-forward` access.
- CPU and GPU smoke paths to prove the cluster, OSMO service, backend operator token, KAI scheduling, Karpenter provisioning, and GPU runtime path are reproducible.

### Validated Robotics Workflows

- OSMO CPU and GPU smoke workflows.
- NVIDIA GR00T fine-tuning workflows, including OSMO workflow submission and distributed EFA training validation.
- OpenPI LoRA fine-tuning examples.
- Cosmos Reason2 NIM and Cosmos augmentation examples.
- Isaac Lab and RSL-RL validation examples.
- A multistage nut pouring pipeline adapted from the upstream OSMO cookbook.

### Reproducibility

- Version pins and compatibility notes in `versions.yaml` and `docs/`.

## Repository Layout

```text
infra/core/        AWS infrastructure IaC
infra/kubernetes/  Kubernetes add-on, OSMO deploy, and GPU node helpers
infra/ingress/     Optional HTTPS admin ingress for OSMO UI
infra/observability/ Optional AMP and AMG observability root
scripts/           repo-level preflight, Terraform lifecycle, security scan, and destroy wrappers
examples/          self-contained OSMO example workflows, docs, and representative artifacts
benchmarks/        platform performance and distributed training benchmark workloads
docs/              architecture, observability, reproducibility, security, version matrix, compatibility
versions.yaml      pinned external versions and tested ranges
```

## Prerequisites

- AWS CLI v2, Terraform, kubectl, Helm, jq, curl, git, and the OSMO CLI.
- An NGC API key with access to the pinned OSMO images in `nvcr.io/nvidia/osmo`.
- A Hugging Face token in `HF_TOKEN`, or `HF_TOKEN_FILE` pointing at a readable token file, for the full nut pouring pipeline.

Provide the NGC API key as an environment variable or a local key file before running `scripts/preflight.sh` or `infra/kubernetes/deploy-osmo.sh`:

```bash
export NGC_API_KEY="<your-ngc-api-key>"
```

The deploy wrapper also accepts a raw key in `~/.nvidia`, or another file path through `NGC_API_KEY_FILE`. Do not commit NGC key files.

## Quick Start

```bash
cp infra/core/terraform.tfvars.example infra/core/terraform.tfvars
scripts/preflight.sh
scripts/deploy-infra.sh
infra/kubernetes/deploy-karpenter.sh
infra/kubernetes/deploy-gpu-operator.sh
infra/kubernetes/deploy-efa-device-plugin.sh
infra/kubernetes/deploy-osmo.sh
infra/kubernetes/validate-platform.sh
examples/run-workflow.sh
```

By default, `scripts/deploy-infra.sh` sets
`cluster_endpoint_public_access_cidrs` to the caller's current public IPv4
`/32`. This keeps the EKS API endpoint reachable for the subsequent
`deploy-karpenter.sh`, GPU Operator, EFA plugin, OSMO, and validation commands
when the deployer is outside the VPC. To keep the endpoint private-only, run the
wrapper from a network path that can reach the private endpoint and set:

```bash
CLUSTER_ENDPOINT_PUBLIC_ACCESS_CIDRS=private scripts/deploy-infra.sh
```

To use a fixed administrator allow list instead of auto-detection:

```bash
CLUSTER_ENDPOINT_PUBLIC_ACCESS_CIDRS=203.0.113.10/32,198.51.100.7/32 \
  scripts/deploy-infra.sh
```

The Karpenter wrapper creates GPU NodePools for the reference G7e OSMO platform
and the validated G6e GR00T multi-node EFA example. NodePools do not launch
instances until a matching workload is submitted.

`examples/run-workflow.sh` submits `examples/smoke/cpu-workflow/workflow.yaml` by default. For the GPU smoke workflow, prewarm a G7e node so OSMO resource validation can observe GPU platform capacity:

```bash
GPU_PREWARM_INSTANCE_TYPE=g7e.2xlarge infra/kubernetes/prewarm-gpu-node.sh
SMOKE_SET_NGC_CREDENTIAL=true \
  WORKFLOW_FILE=examples/smoke/gpu-workflow/workflow.yaml \
  SMOKE_TIMEOUT_ATTEMPTS=180 \
  examples/run-workflow.sh
infra/kubernetes/wait-gpu-node-cleanup.sh
```

Validated example workflows live under [examples/](examples/README.md). Each
example folder keeps its workflow definition, run instructions, validation
notes and representative artifacts, and any Kubernetes templates together.

For local UI access, keep the UI port-forward open:

```bash
kubectl -n osmo port-forward svc/osmo-ui 9001:80
```

Then open <http://127.0.0.1:9001>. The default UI deployment proxies API requests from the UI pod to `osmo-service:80`. Override
`OSMO_UI_API_HOSTNAME` before `infra/kubernetes/deploy-osmo.sh` if using a different private endpoint.

For local CLI or direct API access, keep a separate API port-forward open:

```bash
kubectl -n osmo port-forward svc/osmo-service 9000:80
```

## EFA Modes

The baseline installs the AWS EFA device plugin so EFA-capable GPU nodes can expose `vpc.amazonaws.com/efa`. Installing the plugin is safe on clusters or nodes without EFA support: the upstream chart only schedules the DaemonSet on supported instance labels, so unsupported instances such as `g7e.2xlarge` and `g7e.4xlarge` simply do not register an EFA resource.

Use EFA-enabled mode when a workflow explicitly needs EFA or NCCL/RDMA validation:

```bash
infra/kubernetes/deploy-efa-device-plugin.sh
GPU_PREWARM_INSTANCE_TYPE=g6e.8xlarge \
  GPU_PREWARM_EFA=true \
  infra/kubernetes/prewarm-gpu-node.sh
OSMO_VALIDATE_EFA_DEVICE_PLUGIN=true \
  OSMO_VALIDATE_EFA_NODE=true \
  infra/kubernetes/validate-platform.sh
```

For multi-node EFA validation, run the OSMO NCCL benchmark:

```bash
cd benchmarks/g6e-efa-nccl
osmo workflow submit workflow.yaml --pool default
```

That NCCL benchmark is a transport check. Its in-place and out-of-place
all-reduce lines differ only in whether the input and output buffers share the
same memory, so similar performance is expected. To compare training wall-clock
with and without EFA, run the DDP benchmark:

```bash
cd benchmarks/g6e-efa-ddp
osmo workflow submit workflow.yaml --pool default
```

The DDP benchmark defaults to two `g6e.8xlarge` nodes, one GPU per node, and a
64 MiB gradient payload per rank. The EFA path uses NCCL Libfabric through the
`g6e-l40s-efa` OSMO platform; the non-EFA comparison sets `mode=socket` and
uses the `g6e-l40s` platform to force `NCCL_NET=Socket`.

GPU capacity can be scarce for repeatable multi-node EFA validation. If Spot is
acceptable for a smoke or benchmark run, allow both capacity types:

```bash
KARPENTER_CAPACITY_TYPES=on-demand,spot infra/kubernetes/deploy-karpenter.sh
```

For stricter repeatability, use a targeted EC2 Capacity Reservation or Capacity
Block and redeploy the Karpenter EC2NodeClass with its reservation ID:

```bash
KARPENTER_CAPACITY_RESERVATION_IDS=cr-0123456789abcdef0 \
  infra/kubernetes/deploy-karpenter.sh
```

`KARPENTER_CAPACITY_RESERVATION_IDS` accepts a comma-separated list when the run
uses more than one targeted reservation. Without reserved capacity, Karpenter
may create EFA-capable GPU NodeClaims and still fail at EC2 `CreateFleet` with
`InsufficientInstanceCapacity`.

The core Terraform module opens self-referenced all-traffic ingress and egress
on the EKS node security group because EFA/NCCL traffic requires node-to-node
communication beyond ordinary Kubernetes pod TCP ports.

Use EFA-disabled mode for ordinary single-node GPU examples or GPU-only platforms. In that mode, skip `infra/kubernetes/deploy-efa-device-plugin.sh`, do not request `vpc.amazonaws.com/efa` in workflow pod resources, and leave `OSMO_VALIDATE_EFA_NODE=false`.

Destroy the reference environment when finished:

```bash
scripts/destroy.sh
```

## Current Scope

This reference focuses on the AWS integration layer for NVIDIA OSMO. It is not a general-purpose NVIDIA robotics platform distribution; it demonstrates repeatable AWS infrastructure, EKS GPU scheduling, OSMO deployment, and validated workflow execution for NVIDIA robotics and physical AI workloads.

The current baseline uses S3-backed OSMO workflow and dataset storage plus per-workflow ephemeral task storage.

HTTPS admin UI access is optional under `infra/ingress`. That Terraform root installs AWS Load Balancer Controller, requests an ACM certificate, creates an ALB-backed Kubernetes Ingress for `osmo-ui`, and publishes a Route 53 record. It requires an explicit domain name, hosted zone ID, and non-public source CIDR allow list.

For AWS managed observability, see [infra/observability/](infra/observability/README.md) and [docs/observability.md](docs/observability.md). The deployable path maps OSMO's Prometheus and Grafana observability flow to Amazon Managed Service for Prometheus and Amazon Managed Grafana.

The full NVIDIA nut pouring cookbook is adapted from an external pinned
dependency and checked in as a prepared six-step workflow set. Run it through
the wrapper after the GPU smoke path succeeds:

```bash
export HF_TOKEN_FILE="$HOME/.huggingface/token"
examples/simulation/nut-pouring-pipeline/run.sh
```

The nut pouring wrapper prewarms a `g7e.24xlarge` by default because the
prepared GR00T fine-tune workflow requests `cpu: 64`, `memory: 512Gi`, and
`gpu: 1`. The prepared workflow set preserves upstream resource requests, adds
the AWS G7e OSMO platform, removes only the interactive `sleep infinity` hold
from step 01 so the six-step run can complete unattended, and verifies
Karpenter GPU node cleanup when the workflow finishes.

For bounded validation of the Cosmos augmentation path, set `NUT_POURING_MAX_DEMOS=1` or another small value. Leave it unset for the full upstream cookbook run.

For ad hoc GPU cleanup checks, run:

```bash
infra/kubernetes/wait-gpu-node-cleanup.sh
```

## Upstream Strategy

NVIDIA OSMO remains external and pinned. This repo uses NVIDIA Terraform and documentation only as reference material. AWS-specific implementation, security defaults, deployment scripts, and small representative output artifacts belong here.

See [docs/osmo-compatibility.md](docs/osmo-compatibility.md) for the AWS compatibility note related to [NVIDIA/OSMO PR #894](https://github.com/NVIDIA/OSMO/pull/894).

Detailed sample run results are summarized in the contribution pull request
rather than repeated as per-example log files. Selected workflows keep compact
visual outputs under `artifacts/` so readers can inspect representative results
without downloading raw logs or checkpoints.
