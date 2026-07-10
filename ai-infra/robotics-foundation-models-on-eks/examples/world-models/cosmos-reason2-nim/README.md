# Cosmos Reason2 NIM

Cosmos Reason2 VLM workflow using NVIDIA OSMO's NIM client/server pattern with `nvidia/cosmos-reason2-2b`.

Files:

- [workflow.yaml](workflow.yaml): OSMO workflow definition.
- [artifacts/](artifacts/): representative input preview.

Run the local NIM validation:

On a scale-to-zero cluster, prewarm is required for the local NIM server path
because OSMO validates platform capacity before Karpenter can provision a node.

```bash
cd ai-infra/robotics-foundation-models-on-eks
GPU_PREWARM_INSTANCE_TYPE=g7e.4xlarge infra/kubernetes/prewarm-gpu-node.sh
SMOKE_SET_NGC_CREDENTIAL=true \
  WORKFLOW_FILE=examples/world-models/cosmos-reason2-nim/workflow.yaml \
  SMOKE_TIMEOUT_ATTEMPTS=720 \
  examples/run-workflow.sh
kubectl -n osmo-workflows delete pod aws-osmo-gpu-prewarm --ignore-not-found
infra/kubernetes/wait-gpu-node-cleanup.sh
```

Use `g7e.4xlarge` or larger for the default local server resources. The server requests `cpu: 12`, `memory: 96Gi`, `storage: 256Gi`, and `gpu: 1`.

To call a hosted NIM instead of launching the server task, create `ngc-api-key`
and submit with the external URL. This path uses only the CPU client task, so no
GPU prewarm is required.

```bash
cd ai-infra/robotics-foundation-models-on-eks
osmo workflow submit examples/world-models/cosmos-reason2-nim/workflow.yaml \
  --pool default \
  --set external_nim_server_url=https://integrate.api.nvidia.com
```

The workflow uploads the request JSON, response JSON, answer text, and run manifest. The validation prompt asks Cosmos Reason2 to approve or reject the input video for robotics dataset inclusion and include a short rationale.
