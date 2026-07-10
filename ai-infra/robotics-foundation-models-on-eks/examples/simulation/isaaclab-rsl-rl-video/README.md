# Isaac Lab RSL-RL Video

Isaac Lab RSL-RL workflow for `Isaac-Reach-Franka-v0`. It trains for a bounded number of iterations and exports checkpoint, TensorBoard event files, and before/after videos.

Run it through the repo wrapper:

On a scale-to-zero cluster, prewarm is required for this GPU workflow because
OSMO validates platform capacity before Karpenter can provision a node.

```bash
cd ai-infra/robotics-foundation-models-on-eks
GPU_PREWARM_INSTANCE_TYPE=g7e.2xlarge infra/kubernetes/prewarm-gpu-node.sh
SMOKE_SET_NGC_CREDENTIAL=true \
  WORKFLOW_FILE=examples/simulation/isaaclab-rsl-rl-video/workflow.yaml \
  SMOKE_TIMEOUT_ATTEMPTS=720 \
  examples/run-workflow.sh
infra/kubernetes/wait-gpu-node-cleanup.sh
```

Expected result:

- The workflow exports `before-training.mp4`, `after-training.mp4`, TensorBoard
  event files, and the final checkpoint.
- Expected completion time is `30-35 min` including G7e provisioning and cleanup.
- Representative output: [before/after GIF](artifacts/result/videos/before-after-comparison.gif)

Result:

The example uses `Isaac-Reach-Franka-v0` because it ships in the pinned Isaac Lab image and produces visible artifacts quickly.
