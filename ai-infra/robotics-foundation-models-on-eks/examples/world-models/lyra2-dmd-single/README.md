# Lyra-2.0 DMD Single Sample

Lyra-2.0 DMD single-sample generation and Gaussian-scene trajectory rendering using pinned upstream source and model refs.

Files:

- [workflow.yaml](workflow.yaml): OSMO workflow definition.
- [artifacts/](artifacts/): representative generated-scene preview.

Run the validation:

On a scale-to-zero cluster, prewarm is required for this GPU workflow because
OSMO validates platform capacity before Karpenter can provision a node.

```bash
cd ai-infra/robotics-foundation-models-on-eks
GPU_PREWARM_INSTANCE_TYPE=g7e.4xlarge infra/kubernetes/prewarm-gpu-node.sh
SMOKE_SET_NGC_CREDENTIAL=true \
  SMOKE_SET_HF_CREDENTIAL=true \
  HF_TOKEN_FILE="$HOME/.huggingface/token" \
  WORKFLOW_FILE=examples/world-models/lyra2-dmd-single/workflow.yaml \
  SMOKE_TIMEOUT_ATTEMPTS=1440 \
  examples/run-workflow.sh
kubectl -n osmo-workflows delete pod aws-osmo-gpu-prewarm --ignore-not-found
infra/kubernetes/wait-gpu-node-cleanup.sh
```

Lyra requires Hugging Face access for `nvidia/Lyra-2.0`. The stage-2 VIPE/DA3 reconstruction path imports MoGe for depth and geometry support, so the workflow pins `microsoft/MoGe` separately from the Lyra repository. The default validation deletes generated PLY files before upload and retains MP4 outputs plus the manifest.
