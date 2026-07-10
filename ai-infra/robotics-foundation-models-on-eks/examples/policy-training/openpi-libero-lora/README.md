# OpenPI LIBERO LoRA

PASK-aligned OpenPI LoRA workflow using the upstream `pi0_libero_low_mem_finetune` config and HF `physical-intelligence/libero` dataset episode subset.

Files:

- [workflow.yaml](workflow.yaml): OSMO workflow definition.
- [artifacts/](artifacts/): representative action replay GIF.

Run the bounded E2E validation:

On a scale-to-zero cluster, prewarm is required for this GPU workflow because
OSMO validates platform capacity before Karpenter can provision a node.

```bash
cd ai-infra/robotics-foundation-models-on-eks
GPU_PREWARM_INSTANCE_TYPE=g7e.4xlarge infra/kubernetes/prewarm-gpu-node.sh
SMOKE_SET_NGC_CREDENTIAL=true \
  SMOKE_SET_HF_CREDENTIAL=true \
  HF_TOKEN_FILE="$HOME/.huggingface/token" \
  WORKFLOW_FILE=examples/policy-training/openpi-libero-lora/workflow.yaml \
  SMOKE_TIMEOUT_ATTEMPTS=720 \
  examples/run-workflow.sh
infra/kubernetes/wait-gpu-node-cleanup.sh
```

Use `g7e.4xlarge` for the validated path because the workflow requests `cpu: 8`, `memory: 64Gi`, and `gpu: 1`.

For a longer quality-oriented run matching the upstream OpenPI step-count signal,
submit directly after `huggingface_token` exists in OSMO:

```bash
cd ai-infra/robotics-foundation-models-on-eks
GPU_PREWARM_INSTANCE_TYPE=g7e.4xlarge infra/kubernetes/prewarm-gpu-node.sh
osmo workflow submit examples/policy-training/openpi-libero-lora/workflow.yaml \
  --pool default \
  -t json \
  --set num_train_steps=30000 \
  --set save_interval=30000 \
  --set norm_stats_max_frames=1024 \
  --set batch_size=1 \
  --set gpu_metrics_interval_seconds=10 \
  --set-string output_dataset=openpi-libero-lora-30k-artifacts \
  --set-string experiment_name=aws-osmo-libero-lora-30k \
  --set-string retain_checkpoint_arrays=true
```

After the submitted workflow reaches `COMPLETED`, clean up the G7e nodepool:

```bash
cd ai-infra/robotics-foundation-models-on-eks
infra/kubernetes/wait-gpu-node-cleanup.sh
```

The reference workflow is a single-GPU E2E validation path. It is not a full pi0.5 LIBERO benchmark reproduction.
