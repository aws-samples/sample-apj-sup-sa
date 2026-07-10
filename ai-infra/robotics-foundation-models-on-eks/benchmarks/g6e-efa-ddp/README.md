# G6e EFA DDP Benchmark

OSMO 2-node PyTorch DDP benchmark for comparing EFA-backed NCCL against NCCL
socket networking on the same GPU pool.

This benchmark is intentionally synthetic and communication-heavy. It measures
training step wall-clock for a DDP model with a large gradient payload, not model
quality or end-to-end dataset throughput. Use it when the question is whether
EFA changes multi-node training time for gradient-synchronization-heavy jobs.

Prerequisites:

- `infra/kubernetes/deploy-karpenter.sh` has deployed the G6e NodePool.
- `infra/kubernetes/deploy-efa-device-plugin.sh` has deployed the AWS EFA device plugin.
- `infra/core` has applied the node security group self ingress and egress rules
  required by EFA.
- `infra/kubernetes/deploy-osmo.sh` has configured the `g6e-l40s-efa` and
  `g6e-l40s` OSMO platforms.
- At least two EFA-capable G6e nodes can be provisioned. The EFA platform
  requests `vpc.amazonaws.com/efa: 1`, one GPU per pod, and distinct nodes for
  the two DDP ranks.
- On a scale-to-zero cluster, prewarm is required before submission because OSMO
  validates `g6e-l40s-efa` and `g6e-l40s` capacity before Karpenter can
  provision a node.
- If On-Demand G6e capacity is scarce and Spot is acceptable, redeploy
  Karpenter with
  `KARPENTER_CAPACITY_TYPES=on-demand,spot infra/kubernetes/deploy-karpenter.sh`.
- For repeatable validation, use a targeted EC2 Capacity Reservation or Capacity
  Block and redeploy Karpenter with
  `KARPENTER_CAPACITY_RESERVATION_IDS=cr-... infra/kubernetes/deploy-karpenter.sh`.

Run:

```bash
cd ai-infra/robotics-foundation-models-on-eks
GPU_PREWARM_INSTANCE_TYPE=g6e.8xlarge \
  GPU_PREWARM_EFA=true \
  infra/kubernetes/prewarm-gpu-node.sh

cd benchmarks/g6e-efa-ddp
osmo workflow submit workflow.yaml --pool default
```

For a short smoke run, reduce the gradient payload and measured steps:

If no G6e EFA node is already visible to OSMO, run the same EFA prewarm first.

```bash
cd ai-infra/robotics-foundation-models-on-eks
GPU_PREWARM_INSTANCE_TYPE=g6e.8xlarge \
  GPU_PREWARM_EFA=true \
  infra/kubernetes/prewarm-gpu-node.sh

cd benchmarks/g6e-efa-ddp
osmo workflow submit workflow.yaml --pool default \
  --set param_mib=16 warmup_steps=1 steps=1 \
  --set-string workflow_name=aws-g6e-efa-ddp-smoke \
  --set-string mode=efa platform=g6e-l40s-efa \
  --set-string output_dataset=aws-g6e-efa-ddp-smoke-results
```

The workflow injects [train.py](train.py) into the master and worker tasks, then
uses OSMO `groups` and `{{host:master}}` to start both DDP ranks together.

The runner executes two modes with the same PyTorch training script:

- `efa`: uses the `g6e-l40s-efa` platform and lets NCCL use the AWS OFI
  NCCL/Libfabric path.
- `socket`: set `mode=socket platform=g6e-l40s` so NCCL uses the
  ordinary pod network path.

Example socket comparison:

```bash
cd ai-infra/robotics-foundation-models-on-eks
GPU_PREWARM_INSTANCE_TYPE=g6e.8xlarge infra/kubernetes/prewarm-gpu-node.sh

cd benchmarks/g6e-efa-ddp
osmo workflow submit workflow.yaml --pool default \
  --set-string mode=socket platform=g6e-l40s workflow_name=aws-g6e-ddp-socket
```

After each submitted workflow reaches `COMPLETED`, clean up the G6e nodepool:

```bash
cd ai-infra/robotics-foundation-models-on-eks
KARPENTER_NODEPOOL_NAME=aws-osmo-g6e infra/kubernetes/wait-gpu-node-cleanup.sh
```

Default workload:

- 2 nodes
- 1 GPU per node
- 64 MiB gradient payload per rank
- 1 warmup step
- 3 measured training steps

Validated run on 2026-05-15:

- EFA workflow `aws-g6e-efa-ddp-verified-1`: `avg_step_seconds=0.022148`, dataset `aws-g6e-efa-ddp-verified-results:1`.
- Socket workflow `aws-g6e-ddp-socket-verified-1`: `avg_step_seconds=0.037319`, dataset `aws-g6e-ddp-socket-verified-results:1`.
- The EFA run logged `Selected provider is efa` and `Using network Libfabric`;
  the socket run logged `Using network Socket`.

Representative output:

- [Training-time plot](artifacts/training-time.svg)
