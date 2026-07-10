# Reproducibility

Reproducibility is the main design goal of this repository.

## Rules

- External versions are pinned in `versions.yaml`.
- NGC image pulls are authenticated through `NGC_API_KEY` or `NGC_API_KEY_FILE`; the scripts fail fast when the pinned image registry is `nvcr.io` and no key is available.
- OSMO is not vendored; upgrades happen through explicit PRs with validation notes.
- Terraform owns the AWS infrastructure and scripts own the KAI, Karpenter, GPU Operator, EFA device plugin, and OSMO install sequence.
- The default environment is designed to deploy, validate, run CPU and GPU smoke paths, and destroy cleanly.
- Public EKS API endpoint access is restricted to a narrow allow list. The
  Quick Start `scripts/deploy-infra.sh` wrapper auto-detects the caller's
  current public IPv4 and passes it as `/32` so the remaining deployment
  wrappers can reach the new cluster from outside the VPC. Set
  `CLUSTER_ENDPOINT_PUBLIC_ACCESS_CIDRS=private` only when the deployment host
  can reach the private endpoint.
- The artifact S3 bucket and workload ECR repository default to force delete so ephemeral validation accounts can destroy after smoke tests.
- The runtime secret recovery window defaults to `0` for clean ephemeral teardown; set `secret_recovery_window_in_days` higher for shared or long-lived environments.

## Required Validation

Run these checks before treating a version bump or infrastructure change as valid:

```bash
scripts/preflight.sh
scripts/deploy-infra.sh
infra/kubernetes/deploy-karpenter.sh
infra/kubernetes/deploy-gpu-operator.sh
infra/kubernetes/deploy-efa-device-plugin.sh
infra/kubernetes/deploy-osmo.sh
OSMO_VALIDATE_KARPENTER=true \
  OSMO_VALIDATE_GPU_OPERATOR=true \
  OSMO_VALIDATE_EFA_DEVICE_PLUGIN=true \
  infra/kubernetes/validate-platform.sh
examples/run-workflow.sh
GPU_PREWARM_INSTANCE_TYPE=g7e.2xlarge infra/kubernetes/prewarm-gpu-node.sh
SMOKE_SET_NGC_CREDENTIAL=true \
  WORKFLOW_FILE=examples/smoke/gpu-workflow/workflow.yaml \
  SMOKE_TIMEOUT_ATTEMPTS=180 \
  examples/run-workflow.sh
infra/kubernetes/wait-gpu-node-cleanup.sh
scripts/destroy.sh
```

The clean-account test must verify:

- Terraform fmt and validate succeed.
- The EKS API endpoint is reachable from the deployment host after
  `scripts/deploy-infra.sh`, either through the wrapper's caller `/32` public
  endpoint allow list or through an explicitly private network path.
- NGC API key input is available before OSMO Helm install.
- KAI Scheduler installs from the pinned OCI Helm chart and exposes the real `scheduling.run.ai` PodGroup CRD.
- Karpenter installs from the pinned OCI Helm chart and exposes the G7e and G6e NodePools and EC2NodeClasses.
- The default VPC creates Karpenter-discoverable GPU subnets across the configured region's selected Availability Zones so Karpenter can try multiple zones for On-Demand GPU capacity.
- NVIDIA GPU Operator installs from the pinned Helm chart with driver and toolkit management disabled for the EKS AL2023 NVIDIA AMI.
- AWS EFA device plugin installs from the pinned Helm chart and tolerates the GPU taint so EFA-capable GPU nodes can register `vpc.amazonaws.com/efa`.
- The default system node group leaves workflow-allocatable CPU after OSMO and KAI system pods are reserved.
- OSMO installs on a cluster without Prometheus Operator.
- Backend operator token generation uses `backend-operator`.
- Backend pods do not enter `CrashLoopBackOff`.
- The CPU-only smoke workflow completes.
- The GPU smoke path prewarms a G7e node for OSMO resource validation, submits `examples/smoke/gpu-workflow/workflow.yaml`, verifies the GPU workflow runs `nvidia-smi`, and confirms cleanup removes the G7e NodeClaim and node.
- After GPU workflows complete, `infra/kubernetes/wait-gpu-node-cleanup.sh` removes completed GPU Operator validator pods and residual OSMO workflow pods if present, then confirms that no G7e NodeClaims or nodes remain for the Karpenter GPU NodePool.
- Destroy completes, or retained resources are documented.

## EFA Validation Modes

EFA-disabled validation is the default for ordinary CPU and single-node GPU smoke tests. In this mode, do not request `vpc.amazonaws.com/efa` in workflow resources and leave `OSMO_VALIDATE_EFA_NODE=false`. The GPU smoke path may use smaller G7e sizes such as `g7e.2xlarge` that do not support EFA.

EFA-enabled validation requires two conditions:

- Install the AWS EFA device plugin with `infra/kubernetes/deploy-efa-device-plugin.sh`.
- Prewarm or submit onto an EFA-capable GPU size such as `g6e.8xlarge`, `g7e.8xlarge`, `g7e.12xlarge`, or `g7e.24xlarge`.
- Keep the core Terraform node security group self ingress and egress rules
  enabled so EFA/NCCL traffic can pass between nodes.

After an EFA-capable node is present, run:

```bash
GPU_PREWARM_INSTANCE_TYPE=g6e.8xlarge \
  GPU_PREWARM_EFA=true \
  infra/kubernetes/prewarm-gpu-node.sh
OSMO_VALIDATE_EFA_DEVICE_PLUGIN=true \
  OSMO_VALIDATE_EFA_NODE=true \
  infra/kubernetes/validate-platform.sh
```

If the plugin is installed but the cluster only has EFA-unsupported nodes, validation with `OSMO_VALIDATE_EFA_DEVICE_PLUGIN=true` should still pass, but `OSMO_VALIDATE_EFA_NODE=true` should fail because no node exposes `vpc.amazonaws.com/efa`.

For smoke and benchmark runs where Spot is acceptable, redeploy Karpenter with
both On-Demand and Spot enabled:

```bash
KARPENTER_CAPACITY_TYPES=on-demand,spot infra/kubernetes/deploy-karpenter.sh
```

For stricter repeatable multi-node EFA validation, use targeted EC2 Capacity
Reservation or Capacity Block capacity for the EFA-capable GPU sizes and pass
the reservation ID when deploying Karpenter:

```bash
KARPENTER_CAPACITY_RESERVATION_IDS=cr-0123456789abcdef0 \
  infra/kubernetes/deploy-karpenter.sh
```

`KARPENTER_CAPACITY_RESERVATION_IDS` accepts a comma-separated list when the run
uses more than one targeted reservation. Without reserved capacity, Karpenter
can still reach EC2 `CreateFleet` and fail with `InsufficientInstanceCapacity`
if the region has no available GPU capacity for the requested size and
Availability Zone.

For multi-node collective validation, submit
`benchmarks/g6e-efa-nccl/workflow.yaml` through OSMO. That benchmark requests
EFA and one GPU on two separate G6e nodes and records NCCL Libfabric evidence in
its validation log and output dataset.

For training wall-clock comparison, submit
`benchmarks/g6e-efa-ddp/workflow.yaml` through OSMO. That benchmark runs the same
synthetic PyTorch DDP training step twice: once with EFA requested and NCCL using
Libfabric, and once with `NCCL_NET=Socket` forced for the non-EFA
comparison path.

AWS KMS keys are scheduled for deletion rather than removed immediately. The repo sets a seven-day deletion window for the reference and EKS keys.
