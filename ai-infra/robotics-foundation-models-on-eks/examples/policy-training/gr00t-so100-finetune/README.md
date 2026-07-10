# GR00T SO100 Fine-Tune

PASK-aligned GR00T fine-tune OSMO workflow using NVIDIA `GR00T-N1.6-3B`,
the upstream SO100 `cube_to_bowl_5` data path, and the pinned source ref in
`versions.yaml`.

Files:

- [workflow.yaml](workflow.yaml): OSMO workflow definition.
- [artifacts/](artifacts/): representative before/after replay preview.

Run the bounded E2E validation:

On a scale-to-zero cluster, prewarm is required for this GPU workflow because
OSMO validates platform capacity before Karpenter can provision a node.

```bash
cd ai-infra/robotics-foundation-models-on-eks
GPU_PREWARM_INSTANCE_TYPE=g7e.8xlarge infra/kubernetes/prewarm-gpu-node.sh
SMOKE_SET_NGC_CREDENTIAL=true \
  SMOKE_SET_HF_CREDENTIAL=true \
  HF_TOKEN_FILE="$HOME/.huggingface/token" \
  WORKFLOW_FILE=examples/policy-training/gr00t-so100-finetune/workflow.yaml \
  SMOKE_TIMEOUT_ATTEMPTS=720 \
  examples/run-workflow.sh
infra/kubernetes/wait-gpu-node-cleanup.sh
```

Use `g7e.8xlarge` for the validated path because the workflow requests `cpu: 16`, `memory: 96Gi`, and `gpu: 1`.

For a longer quality-oriented run matching the SO-101 tutorial checkpoint scale,
submit directly after `huggingface_token` exists in OSMO:

```bash
cd ai-infra/robotics-foundation-models-on-eks
GPU_PREWARM_INSTANCE_TYPE=g7e.8xlarge infra/kubernetes/prewarm-gpu-node.sh
osmo workflow submit examples/policy-training/gr00t-so100-finetune/workflow.yaml \
  --pool default \
  -t json \
  --set max_steps=10000 \
  --set save_steps=10000 \
  --set save_total_limit=1 \
  --set global_batch_size=1 \
  --set gpu_metrics_interval_seconds=10 \
  --set-string output_dataset=gr00t-finetune-10k-artifacts \
  --set-string retain_model_weights=true
```

After the submitted workflow reaches `COMPLETED`, clean up the G7e nodepool:

```bash
cd ai-infra/robotics-foundation-models-on-eks
infra/kubernetes/wait-gpu-node-cleanup.sh
```

The default workflow values are intentionally small because this repo uses them to validate OSMO execution, credentials, scheduling, artifact upload, and cleanup. They are not a full policy-quality benchmark.
