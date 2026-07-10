#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=../../scripts/common.sh
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/common.sh"

require_cmds kubectl helm terraform

GPU_OPERATOR_CHART_REPOSITORY="${GPU_OPERATOR_CHART_REPOSITORY:-$(version_value gpu_operator_chart_repository)}"
GPU_OPERATOR_CHART_VERSION="${GPU_OPERATOR_CHART_VERSION:-$(version_value gpu_operator_chart_version)}"
GPU_OPERATOR_NAMESPACE="${GPU_OPERATOR_NAMESPACE:-$(version_value gpu_operator_namespace)}"
GPU_OPERATOR_RELEASE_NAME="${GPU_OPERATOR_RELEASE_NAME:-$(version_value gpu_operator_release_name)}"

VALUES_FILE="$(mktemp)"
trap 'rm -f "${VALUES_FILE}"' EXIT

configure_kubectl

cat >"${VALUES_FILE}" <<'YAML'
driver:
  enabled: false
toolkit:
  enabled: false
dcgmExporter:
  enabled: true
  serviceMonitor:
    enabled: false
operator:
  defaultRuntime: containerd
daemonsets:
  tolerations:
    - key: nvidia.com/gpu
      operator: Exists
      effect: NoSchedule
YAML

log "deploying NVIDIA GPU Operator ${GPU_OPERATOR_CHART_VERSION}"
helm repo add nvidia "${GPU_OPERATOR_CHART_REPOSITORY}" --force-update >/dev/null
helm repo update nvidia >/dev/null
helm upgrade --install "${GPU_OPERATOR_RELEASE_NAME}" nvidia/gpu-operator \
  --namespace "${GPU_OPERATOR_NAMESPACE}" \
  --create-namespace \
  --version "${GPU_OPERATOR_CHART_VERSION}" \
  --values "${VALUES_FILE}" \
  --wait \
  --timeout 15m

kubectl -n "${GPU_OPERATOR_NAMESPACE}" rollout status deployment/gpu-operator --timeout=10m
kubectl wait --for=condition=Established crd/clusterpolicies.nvidia.com --timeout=5m
kubectl get clusterpolicy cluster-policy >/dev/null

log "NVIDIA GPU Operator deployment completed"
