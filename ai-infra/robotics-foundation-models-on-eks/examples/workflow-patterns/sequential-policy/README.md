# Sequential Policy

Small CPU/GPU/CPU workflow that models a typical policy pipeline shape: inspect dataset, run a GPU policy checkpoint task, then package release artifacts.

Run it through the repo wrapper:

This workflow includes a GPU step. On a scale-to-zero cluster, prewarm is
required before submission because OSMO validates platform capacity before
Karpenter can provision a node.

```bash
cd ai-infra/robotics-foundation-models-on-eks
GPU_PREWARM_INSTANCE_TYPE=g7e.2xlarge infra/kubernetes/prewarm-gpu-node.sh
SMOKE_SET_NGC_CREDENTIAL=true \
  WORKFLOW_FILE=examples/workflow-patterns/sequential-policy/workflow.yaml \
  SMOKE_TIMEOUT_ATTEMPTS=180 \
  examples/run-workflow.sh
infra/kubernetes/wait-gpu-node-cleanup.sh
```

Expected result:

- The CPU inspect step, GPU checkpoint step, and CPU package step complete in order.
- Expected completion time is `10-15 min` including G7e provisioning and cleanup.

This is a workflow-shape example. Replace the GPU task body with a real GR00T, OpenPI, or Isaac Lab training command for a model-specific run.
