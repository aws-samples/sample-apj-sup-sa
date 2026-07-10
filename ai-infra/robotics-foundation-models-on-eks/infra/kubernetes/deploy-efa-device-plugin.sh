#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=../../scripts/common.sh
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/common.sh"

require_cmds kubectl helm jq terraform

EFA_DEVICE_PLUGIN_CHART_REPOSITORY="${EFA_DEVICE_PLUGIN_CHART_REPOSITORY:-$(version_value efa_device_plugin_chart_repository)}"
EFA_DEVICE_PLUGIN_CHART_VERSION="${EFA_DEVICE_PLUGIN_CHART_VERSION:-$(version_value efa_device_plugin_chart_version)}"
EFA_DEVICE_PLUGIN_IMAGE_TAG="${EFA_DEVICE_PLUGIN_IMAGE_TAG:-$(version_value efa_device_plugin_image_tag)}"
EFA_DEVICE_PLUGIN_NAMESPACE="${EFA_DEVICE_PLUGIN_NAMESPACE:-$(version_value efa_device_plugin_namespace)}"
EFA_DEVICE_PLUGIN_RELEASE_NAME="${EFA_DEVICE_PLUGIN_RELEASE_NAME:-$(version_value efa_device_plugin_release_name)}"

VALUES_FILE="$(mktemp)"
trap 'rm -f "${VALUES_FILE}"' EXIT

configure_kubectl

cat >"${VALUES_FILE}" <<YAML
image:
  tag: "${EFA_DEVICE_PLUGIN_IMAGE_TAG}"
tolerations:
  - key: nvidia.com/gpu
    operator: Exists
    effect: NoSchedule
YAML

log "deploying AWS EFA device plugin chart ${EFA_DEVICE_PLUGIN_CHART_VERSION} with image ${EFA_DEVICE_PLUGIN_IMAGE_TAG}"
helm repo add eks "${EFA_DEVICE_PLUGIN_CHART_REPOSITORY}" --force-update >/dev/null
helm repo update eks >/dev/null
helm upgrade --install "${EFA_DEVICE_PLUGIN_RELEASE_NAME}" eks/aws-efa-k8s-device-plugin \
  --namespace "${EFA_DEVICE_PLUGIN_NAMESPACE}" \
  --create-namespace \
  --version "${EFA_DEVICE_PLUGIN_CHART_VERSION}" \
  --values "${VALUES_FILE}" \
  --wait \
  --timeout 10m

if [[ "$(kubectl -n "${EFA_DEVICE_PLUGIN_NAMESPACE}" get daemonset "${EFA_DEVICE_PLUGIN_RELEASE_NAME}" -o jsonpath='{.status.desiredNumberScheduled}')" -gt 0 ]]; then
  kubectl -n "${EFA_DEVICE_PLUGIN_NAMESPACE}" rollout status "daemonset/${EFA_DEVICE_PLUGIN_RELEASE_NAME}" --timeout=10m
fi

if ! kubectl -n "${EFA_DEVICE_PLUGIN_NAMESPACE}" get daemonset "${EFA_DEVICE_PLUGIN_RELEASE_NAME}" -o json |
  jq -e 'any(.spec.template.spec.tolerations[]?; .key == "nvidia.com/gpu" and .operator == "Exists" and .effect == "NoSchedule")' >/dev/null; then
  die "EFA device plugin does not tolerate the GPU taint"
fi

RUNNING_IMAGE_TAG="$(kubectl -n "${EFA_DEVICE_PLUGIN_NAMESPACE}" get daemonset "${EFA_DEVICE_PLUGIN_RELEASE_NAME}" -o json |
  jq -r '.spec.template.spec.containers[] | select(.name == "aws-efa-k8s-device-plugin") | .image | split(":")[-1]')"
[[ "${RUNNING_IMAGE_TAG}" == "${EFA_DEVICE_PLUGIN_IMAGE_TAG}" ]] ||
  die "EFA device plugin image tag is ${RUNNING_IMAGE_TAG}, expected ${EFA_DEVICE_PLUGIN_IMAGE_TAG}"

log "AWS EFA device plugin deployment completed"
