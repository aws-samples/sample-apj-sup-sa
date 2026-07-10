# G6e EFA NCCL Benchmark

OSMO 2-node PyTorch/NCCL all-reduce benchmark for validating that AWS EFA is
usable for multi-node GPU collectives on the reference G6e pool.

The workflow launches one rank per node with OSMO `groups` and
`{{host:master}}`, then measures `torch.distributed.all_reduce` over a bounded
set of payload sizes. This keeps the same NCCL/Libfabric validation intent
without a Kubernetes-native SSH/MPI launcher.

Prerequisites:

- `infra/kubernetes/deploy-karpenter.sh` has deployed the G6e NodePool.
- `infra/kubernetes/deploy-efa-device-plugin.sh` has deployed the AWS EFA device plugin.
- `infra/core` has applied the node security group self ingress and egress rules
  required by EFA.
- `infra/kubernetes/deploy-osmo.sh` has configured the `g6e-l40s-efa` and
  `g6e-l40s` OSMO platforms.
- At least two EFA-capable G6e nodes can be provisioned. The benchmark requests
  `vpc.amazonaws.com/efa: 1`, one GPU per rank, and distinct nodes for the two
  ranks.
- On a scale-to-zero cluster, prewarm is required before submission because OSMO
  validates `g6e-l40s-efa` and `g6e-l40s` capacity before Karpenter can
  provision a node.

Run:

```bash
cd ai-infra/robotics-foundation-models-on-eks
GPU_PREWARM_INSTANCE_TYPE=g6e.8xlarge \
  GPU_PREWARM_EFA=true \
  infra/kubernetes/prewarm-gpu-node.sh

cd benchmarks/g6e-efa-nccl
osmo workflow submit workflow.yaml --pool default
```

For a Socket baseline on the same G6e pool:

```bash
cd ai-infra/robotics-foundation-models-on-eks
GPU_PREWARM_INSTANCE_TYPE=g6e.8xlarge infra/kubernetes/prewarm-gpu-node.sh

cd benchmarks/g6e-efa-nccl
osmo workflow submit workflow.yaml --pool default \
  --set-string mode=socket platform=g6e-l40s workflow_name=aws-g6e-nccl-socket
```

After each submitted workflow reaches `COMPLETED`, clean up the G6e nodepool:

```bash
cd ai-infra/robotics-foundation-models-on-eks
KARPENTER_NODEPOOL_NAME=aws-osmo-g6e infra/kubernetes/wait-gpu-node-cleanup.sh
```

The master task writes `result.json`, `bandwidth.csv`, and `master.log` to the
configured output dataset. For training wall-clock comparison with and without
EFA, use [g6e-efa-ddp](../g6e-efa-ddp/README.md).

Default workload:

- 2 nodes
- 1 GPU per node
- Message sizes: 8, 16, 32, and 64 MiB per rank
- 2 warmup all-reduces per size
- 10 measured all-reduces per size

Validated run on 2026-05-15:

- EFA workflow `aws-g6e-efa-nccl-verified-1`, dataset `aws-g6e-efa-nccl-verified-results:1`.
- The run logged `Selected provider is efa` and `Using network Libfabric`.
- Measured bus bandwidth was `5.865`, `5.807`, `5.816`, and `5.822 GiB/s`
  for 8, 16, 32, and 64 MiB per rank respectively.

Representative output:

- [Bandwidth plot](artifacts/nccl-efa-2node-bandwidth.svg)
