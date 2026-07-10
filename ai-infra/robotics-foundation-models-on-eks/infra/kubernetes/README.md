# Kubernetes Deployment Wrappers

This directory contains Kubernetes-facing deployment and validation wrappers for
the reference environment.

Terraform remains under `infra/core`, `infra/ingress`, and
`infra/observability`. Repo-level preflight, core Terraform lifecycle, public
ingress scan, and destroy wrappers remain under `scripts/`.

Wrappers:

- `deploy-karpenter.sh`: installs Karpenter and applies the GPU
  `EC2NodeClass` and `NodePool` resources. The reference deploy creates the
  G7e pool used by OSMO GPU workflows and a G6e pool used by the validated
  GR00T multi-node EFA example.
- `deploy-gpu-operator.sh`: installs the NVIDIA GPU Operator against the pinned
  EKS AL2023 NVIDIA AMI path.
- `deploy-efa-device-plugin.sh`: installs the AWS EFA device plugin with the
  GPU taint toleration used by both GPU pools.
- `deploy-kai.sh`: installs KAI Scheduler.
- `deploy-osmo.sh`: installs OSMO and configures the AWS G7e platform.
- `validate-platform.sh`: validates the deployed Kubernetes and OSMO platform.
- `prewarm-gpu-node.sh`: creates a temporary GPU workload pod so OSMO can see
  G7e capacity before submitting GPU workflows.
- `wait-gpu-node-cleanup.sh`: removes the prewarm pod, deletes residual OSMO
  workflow pods after the workflow has completed, and verifies Karpenter cleaned
  up GPU nodes.
