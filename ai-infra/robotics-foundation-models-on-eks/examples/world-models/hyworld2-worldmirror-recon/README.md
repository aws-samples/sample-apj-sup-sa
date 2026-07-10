# HY-World 2.0 WorldMirror Reconstruction

HY-World 2.0 WorldMirror reconstruction workflow using the upstream `Dining_Table` sample and pinned source/model refs.

Files:

- [workflow.yaml](workflow.yaml): OSMO workflow definition.
- [artifacts/](artifacts/): representative point-cloud preview.

Run the validation:

On a scale-to-zero cluster, prewarm is required for this GPU workflow because
OSMO validates platform capacity before Karpenter can provision a node.

```bash
cd ai-infra/robotics-foundation-models-on-eks
GPU_PREWARM_INSTANCE_TYPE=g7e.4xlarge infra/kubernetes/prewarm-gpu-node.sh
SMOKE_SET_NGC_CREDENTIAL=true \
  WORKFLOW_FILE=examples/world-models/hyworld2-worldmirror-recon/workflow.yaml \
  SMOKE_TIMEOUT_ATTEMPTS=720 \
  examples/run-workflow.sh
kubectl -n osmo-workflows delete pod aws-osmo-gpu-prewarm --ignore-not-found
infra/kubernetes/wait-gpu-node-cleanup.sh
```

The default run keeps `gaussians.ply` and `points.ply` in the OSMO dataset, but only lightweight previews are committed to this repository.
