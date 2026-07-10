# GR00T SO100 EFA Multi-Node Fine-Tune

OSMO GR00T fine-tune smoke for validating distributed EFA-capable GPU training
with a real robotics training stack.

Status: validated with real two-node GR00T training over NCCL Libfabric/EFA on
G6e with the AWS DLC image that matches the pinned Isaac-GR00T runtime.

This distributed example uses the same pinned Isaac-GR00T source ref and
SO100 `cube_to_bowl_5` demo data as the single-node fine-tune workflow, but
runs the training command through `torchrun` across two EFA-capable OSMO tasks.

The default image is intentionally not the repo-wide PyTorch 2.9/cu130 DLC.
Isaac-GR00T N1.6 pins `torch==2.7.1`, CUDA 12.8 wheels, NCCL 2.26, and
`flash-attn==2.7.4.post1`. This example therefore defaults to
`public.ecr.aws/deep-learning-containers/pytorch-training:2.7.1-gpu-py312-cu128-ubuntu22.04-ec2-v1.37`
so the in-container GR00T lockfile and the AWS OFI/NCCL stack stay aligned.
The AWS Physical AI Scaffolding Kit GR00T sample follows the same upstream
GR00T `uv`/`flash-attn` dependency path, but its Slurm script is single-node
(`SBATCH --nodes=1`), so this example keeps the two-node launch logic in the
OSMO workflow.

Prerequisites:

- `infra/kubernetes/deploy-karpenter.sh` has deployed an EFA-capable G6e
  NodePool.
- `infra/kubernetes/deploy-efa-device-plugin.sh` has deployed the AWS EFA
  device plugin.
- `infra/core` has applied node security group self ingress and egress rules
  required by EFA.
- `infra/kubernetes/deploy-osmo.sh` has configured the `g6e-l40s-efa` OSMO
  platform.
- At least two EFA-capable G6e nodes can be provisioned.
- If On-Demand GPU capacity is scarce and Spot is acceptable, redeploy
  Karpenter with
  `KARPENTER_CAPACITY_TYPES=on-demand,spot infra/kubernetes/deploy-karpenter.sh`.
- For repeatable validation, use a targeted EC2 Capacity Reservation or Capacity
  Block and redeploy Karpenter with
  `KARPENTER_CAPACITY_RESERVATION_IDS=cr-... infra/kubernetes/deploy-karpenter.sh`.
- The OSMO credential `huggingface_token` contains a Hugging Face token under
  the `token` key.

Run the bounded smoke:

On a scale-to-zero cluster, prewarm is required for this EFA workflow because
OSMO validates `g6e-l40s-efa` capacity before Karpenter can provision a node.
The workflow still requests two distinct EFA nodes for the training ranks.

```bash
cd ai-infra/robotics-foundation-models-on-eks
GPU_PREWARM_INSTANCE_TYPE=g6e.8xlarge \
  GPU_PREWARM_EFA=true \
  infra/kubernetes/prewarm-gpu-node.sh

(
  cd examples/policy-training/gr00t-so100-efa-multinode-finetune
  osmo workflow submit workflow.yaml --pool default
)
```

After the submitted workflow reaches `COMPLETED`, clean up the G6e nodepool:

```bash
cd ai-infra/robotics-foundation-models-on-eks
KARPENTER_NODEPOOL_NAME=aws-osmo-g6e infra/kubernetes/wait-gpu-node-cleanup.sh
```

The workflow injects [entry.sh](entry.sh) and the local Isaac-GR00T
compatibility patches under [patches/](patches/). OSMO provides the master host
with `{{host:master}}`, schedules both ranks through the `g6e-l40s-efa`
platform, and uploads rank-0 artifacts to the configured output dataset.

Default workload:

- 2 G6e EFA nodes selected by the `g6e-l40s-efa` OSMO platform
- 1 GPU per node
- `torchrun --nnodes=2 --nproc_per_node=1`
- `nvidia/GR00T-N1.6-3B`
- `demo_data/cube_to_bowl_5`
- `max_steps=2`
- `global_batch_size=2`
- Per pod request: `cpu=8`, `memory=64Gi`, `gpu=1`,
  `vpc.amazonaws.com/efa=1`

The OSMO platform requires distinct Kubernetes nodes with pod anti-affinity and
requests `vpc.amazonaws.com/efa=1` for each task. The workflow defaults to
`retain_model_weights=false` so artifact uploads keep logs, manifests, and
timing data without uploading the large checkpoint weights.

Validated OSMO smoke on 2026-05-15:

- Workflow `aws-gr00t-efa-multinode-osmo-smoke-cleanup-1` completed on two
  `g6e.8xlarge` nodes.
- Output dataset `gr00t-efa-multinode-osmo-smoke-cleanup-artifacts:1` uploaded
  `621.0 KiB` with `retain_model_weights=false`.
- Master and worker logs both showed `Selected provider is efa`,
  `Using network Libfabric`, and `GR00T_EFA_MULTINODE_OK`.
- The bounded smoke logged `train_runtime=197.942` seconds for `max_steps=1`.

Measured aligned-image profile comparison:

| Run | Placement | NCCL transport | GPUs | Global batch | Batch/GPU | Model load | HF train runtime | Train samples/s |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Single-GPU baseline | 1x g6e.8xlarge | Single rank | 1 | 2 | 2 | 22.89s | 169.2723s | 0.059 |
| Two-node EFA, same per-GPU batch | 2x g6e.8xlarge | Libfabric/EFA | 2 | 4 | 2 | 23.10s | 190.1897s | 0.105 |
| Two-node Socket, same per-GPU batch | 2x g6e.8xlarge | Socket | 2 | 4 | 2 | 24.48s | 214.4007s | 0.093 |

The model load, processor/dataset setup, trainer init, train loop wall time,
and Hugging Face train metrics are captured separately in each rank's
`phase-timing.json`. Comparing train loop metrics only with batch per GPU held
at `2`, the two-node EFA run improved throughput by `1.78x` versus the
single-GPU baseline and was about `1.13x` faster than the two-node Socket
baseline. G6e reported `nccl_gdrdma=false`, so this validates the EFA
Libfabric path on the documented GR00T runtime.

OSMO uploads the rank-0 output dataset after training. This sample does not
commit raw per-rank JSON, logs, or checkpoints.

The pinned Isaac-GR00T ref defaults to `torchcodec`, and its lockfile uses the
officially compatible `torch==2.7.1` and `torchcodec==0.4.0` pairing. The AWS
DLC is Python 3.12 based, however, and runtime validation showed `torchcodec`
can still fail to load because `libpython3.12.so.1.0` is not present in the
image. The entrypoint therefore runs a small H.264 `torchcodec` decode smoke
after `uv sync`. If that smoke passes, the example sets `GR00T_VIDEO_BACKEND`
to `torchcodec`; if it fails, the example sets `GR00T_VIDEO_BACKEND=pyav` and
uses the local
[pyav fallback patch](patches/isaac-gr00t-video-indices-pyav-fallback.patch)
to add indexed-frame reads for the pinned GR00T ref. This avoids the very slow
upstream `ffmpeg` indexed-frame path while keeping the source change scoped to
the missing backend branch. Set `GR00T_VIDEO_BACKEND=torchcodec`,
`GR00T_VIDEO_BACKEND=pyav`, or `GR00T_VIDEO_BACKEND=ffmpeg` to force a backend.

Useful overrides:

```bash
cd ai-infra/robotics-foundation-models-on-eks
GPU_PREWARM_INSTANCE_TYPE=g6e.8xlarge \
  GPU_PREWARM_EFA=true \
  infra/kubernetes/prewarm-gpu-node.sh

osmo workflow submit examples/policy-training/gr00t-so100-efa-multinode-finetune/workflow.yaml \
  --pool default \
  --set max_steps=100 save_steps=100 global_batch_size=2 \
  --set-string output_dataset=gr00t-efa-multinode-100-step-artifacts
```

Use the same G6e cleanup command above after the override workflow reaches
`COMPLETED`.

Sample run results are summarized in the contribution pull request.
