#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=../../scripts/common.sh
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/common.sh"

require_cmds aws kubectl helm jq terraform

configure_kubectl

OSMO_NAMESPACE="$(terraform_output osmo_namespace)"
OSMO_VALIDATE_WEB_UI="${OSMO_VALIDATE_WEB_UI:-true}"
OSMO_VALIDATE_KAI="${OSMO_VALIDATE_KAI:-true}"
OSMO_VALIDATE_KARPENTER="${OSMO_VALIDATE_KARPENTER:-false}"
OSMO_VALIDATE_GPU_OPERATOR="${OSMO_VALIDATE_GPU_OPERATOR:-false}"
OSMO_VALIDATE_EFA_DEVICE_PLUGIN="${OSMO_VALIDATE_EFA_DEVICE_PLUGIN:-false}"
OSMO_VALIDATE_GPU_NODE="${OSMO_VALIDATE_GPU_NODE:-false}"
OSMO_VALIDATE_EFA_NODE="${OSMO_VALIDATE_EFA_NODE:-false}"
OSMO_VALIDATE_KAI_BIND_LOG="${OSMO_VALIDATE_KAI_BIND_LOG:-false}"
OSMO_KAI_BIND_LOG_SINCE="${OSMO_KAI_BIND_LOG_SINCE:-6h}"
OSMO_INTERNAL_ROUTER_NAME="${OSMO_INTERNAL_ROUTER_NAME:-osmo-internal-router}"

if [[ "${OSMO_VALIDATE_KARPENTER}" == "true" ]]; then
  KARPENTER_NAMESPACE="${KARPENTER_NAMESPACE:-$(version_value karpenter_namespace)}"
  KARPENTER_RELEASE_NAME="${KARPENTER_RELEASE_NAME:-$(version_value karpenter_release_name)}"
  KARPENTER_NODEPOOL_NAME="${KARPENTER_NODEPOOL_NAME:-$(version_value karpenter_nodepool_name)}"
  KARPENTER_EC2NODECLASS_NAME="${KARPENTER_EC2NODECLASS_NAME:-$(version_value karpenter_ec2nodeclass_name)}"
  KARPENTER_G6E_NODEPOOL_NAME="${KARPENTER_G6E_NODEPOOL_NAME:-$(version_value karpenter_g6e_nodepool_name)}"
  KARPENTER_G6E_EC2NODECLASS_NAME="${KARPENTER_G6E_EC2NODECLASS_NAME:-$(version_value karpenter_g6e_ec2nodeclass_name)}"
  KARPENTER_DEPLOY_G6E_NODEPOOL="${KARPENTER_DEPLOY_G6E_NODEPOOL:-true}"

  helm status "${KARPENTER_RELEASE_NAME}" --namespace "${KARPENTER_NAMESPACE}" >/dev/null
  kubectl -n "${KARPENTER_NAMESPACE}" rollout status "deployment/${KARPENTER_RELEASE_NAME}" --timeout=10m >/dev/null
  kubectl get crd nodepools.karpenter.sh >/dev/null
  kubectl get crd nodeclaims.karpenter.sh >/dev/null
  kubectl get crd ec2nodeclasses.karpenter.k8s.aws >/dev/null
  kubectl wait --for=condition=Ready "nodepool/${KARPENTER_NODEPOOL_NAME}" --timeout=5m >/dev/null
  kubectl wait --for=condition=Ready "ec2nodeclass/${KARPENTER_EC2NODECLASS_NAME}" --timeout=5m >/dev/null
  if [[ "${KARPENTER_DEPLOY_G6E_NODEPOOL}" == "true" ]]; then
    kubectl wait --for=condition=Ready "nodepool/${KARPENTER_G6E_NODEPOOL_NAME}" --timeout=5m >/dev/null
    kubectl wait --for=condition=Ready "ec2nodeclass/${KARPENTER_G6E_EC2NODECLASS_NAME}" --timeout=5m >/dev/null
  fi
fi

if [[ "${OSMO_VALIDATE_GPU_OPERATOR}" == "true" ]]; then
  GPU_OPERATOR_NAMESPACE="${GPU_OPERATOR_NAMESPACE:-$(version_value gpu_operator_namespace)}"
  GPU_OPERATOR_RELEASE_NAME="${GPU_OPERATOR_RELEASE_NAME:-$(version_value gpu_operator_release_name)}"

  helm status "${GPU_OPERATOR_RELEASE_NAME}" --namespace "${GPU_OPERATOR_NAMESPACE}" >/dev/null
  kubectl -n "${GPU_OPERATOR_NAMESPACE}" rollout status deployment/gpu-operator --timeout=10m >/dev/null
  kubectl get clusterpolicy cluster-policy >/dev/null
fi

if [[ "${OSMO_VALIDATE_EFA_DEVICE_PLUGIN}" == "true" ]]; then
  EFA_DEVICE_PLUGIN_NAMESPACE="${EFA_DEVICE_PLUGIN_NAMESPACE:-$(version_value efa_device_plugin_namespace)}"
  EFA_DEVICE_PLUGIN_RELEASE_NAME="${EFA_DEVICE_PLUGIN_RELEASE_NAME:-$(version_value efa_device_plugin_release_name)}"
  EFA_DEVICE_PLUGIN_IMAGE_TAG="${EFA_DEVICE_PLUGIN_IMAGE_TAG:-$(version_value efa_device_plugin_image_tag)}"

  helm status "${EFA_DEVICE_PLUGIN_RELEASE_NAME}" --namespace "${EFA_DEVICE_PLUGIN_NAMESPACE}" >/dev/null
  kubectl -n "${EFA_DEVICE_PLUGIN_NAMESPACE}" get daemonset "${EFA_DEVICE_PLUGIN_RELEASE_NAME}" >/dev/null
  kubectl -n "${EFA_DEVICE_PLUGIN_NAMESPACE}" get daemonset "${EFA_DEVICE_PLUGIN_RELEASE_NAME}" -o json |
    jq -e 'any(.spec.template.spec.tolerations[]?; .key == "nvidia.com/gpu" and .operator == "Exists" and .effect == "NoSchedule")' >/dev/null ||
    die "EFA device plugin does not tolerate the GPU taint"
  RUNNING_IMAGE_TAG="$(kubectl -n "${EFA_DEVICE_PLUGIN_NAMESPACE}" get daemonset "${EFA_DEVICE_PLUGIN_RELEASE_NAME}" -o json |
    jq -r '.spec.template.spec.containers[] | select(.name == "aws-efa-k8s-device-plugin") | .image | split(":")[-1]')"
  [[ "${RUNNING_IMAGE_TAG}" == "${EFA_DEVICE_PLUGIN_IMAGE_TAG}" ]] ||
    die "EFA device plugin image tag is ${RUNNING_IMAGE_TAG}, expected ${EFA_DEVICE_PLUGIN_IMAGE_TAG}"
fi

if [[ "${OSMO_VALIDATE_KAI}" == "true" ]]; then
  KAI_SCHEDULER_NAMESPACE="${KAI_SCHEDULER_NAMESPACE:-$(version_value kai_scheduler_namespace)}"
  KAI_SCHEDULER_RELEASE_NAME="${KAI_SCHEDULER_RELEASE_NAME:-$(version_value kai_scheduler_release_name)}"
  KAI_SCHEDULER_DEPLOYMENT="${KAI_SCHEDULER_DEPLOYMENT:-kai-scheduler-default}"
  KAI_SCHEDULER_SERVICE_ACCOUNT="${KAI_SCHEDULER_SERVICE_ACCOUNT:-scheduler}"
  KAI_EXPECTED_DEPLOYMENTS="${KAI_EXPECTED_DEPLOYMENTS:-kai-operator admission binder pod-grouper podgroup-controller queue-controller kai-scheduler-default}"

  helm status "${KAI_SCHEDULER_RELEASE_NAME}" --namespace "${KAI_SCHEDULER_NAMESPACE}" >/dev/null
  kubectl get crd podgroups.scheduling.run.ai >/dev/null
  kubectl get queue default-queue >/dev/null
  for deployment in ${KAI_EXPECTED_DEPLOYMENTS}; do
    kubectl -n "${KAI_SCHEDULER_NAMESPACE}" rollout status "deployment/${deployment}" --timeout=10m >/dev/null
  done

  SCHEDULER_SA_CREATED_AT="$(kubectl -n "${KAI_SCHEDULER_NAMESPACE}" get serviceaccount "${KAI_SCHEDULER_SERVICE_ACCOUNT}" \
    -o jsonpath='{.metadata.creationTimestamp}')"
  SCHEDULER_SELECTOR="$(kubectl -n "${KAI_SCHEDULER_NAMESPACE}" get deployment "${KAI_SCHEDULER_DEPLOYMENT}" -o json |
    jq -r '.spec.selector.matchLabels | to_entries | map("\(.key)=\(.value)") | join(",")')"

  while IFS=$'\t' read -r pod_name pod_started_at; do
    [[ -n "${pod_started_at}" ]] || die "KAI scheduler pod has no start time: ${pod_name}"
    if [[ "${pod_started_at}" < "${SCHEDULER_SA_CREATED_AT}" ]]; then
      die "KAI scheduler pod ${pod_name} started before its service account was recreated; rerun infra/kubernetes/deploy-kai.sh"
    fi
  done < <(kubectl -n "${KAI_SCHEDULER_NAMESPACE}" get pods -l "${SCHEDULER_SELECTOR}" -o json |
    jq -r '.items[] | [.metadata.name, (.status.startTime // "")] | @tsv')
fi

helm status osmo-service --namespace "${OSMO_NAMESPACE}" >/dev/null
helm status osmo-backend --namespace "${OSMO_NAMESPACE}" >/dev/null
kubectl -n "${OSMO_NAMESPACE}" rollout status "deployment/${OSMO_INTERNAL_ROUTER_NAME}" --timeout=5m >/dev/null
kubectl -n "${OSMO_NAMESPACE}" get service "${OSMO_INTERNAL_ROUTER_NAME}" >/dev/null
if [[ "${OSMO_VALIDATE_WEB_UI}" == "true" ]]; then
  helm status osmo-ui --namespace "${OSMO_NAMESPACE}" >/dev/null
  kubectl -n "${OSMO_NAMESPACE}" rollout status deployment/osmo-ui --timeout=10m >/dev/null
  kubectl -n "${OSMO_NAMESPACE}" get service osmo-ui >/dev/null
fi

kubectl wait --for=condition=Ready nodes --all --timeout=10m

if [[ "${OSMO_VALIDATE_GPU_NODE}" == "true" ]]; then
  GPU_ALLOCATABLE_TOTAL="$(kubectl get nodes -o json | jq '[.items[].status.allocatable["nvidia.com/gpu"]? // "0" | tonumber] | add')"
  [[ "${GPU_ALLOCATABLE_TOTAL}" -ge 1 ]] || die "no schedulable node exposes nvidia.com/gpu"
fi

if [[ "${OSMO_VALIDATE_EFA_NODE}" == "true" ]]; then
  EFA_ALLOCATABLE_TOTAL="$(kubectl get nodes -o json | jq '[.items[].status.allocatable["vpc.amazonaws.com/efa"]? // "0" | tonumber] | add')"
  [[ "${EFA_ALLOCATABLE_TOTAL}" -ge 1 ]] || die "no schedulable node exposes vpc.amazonaws.com/efa"
fi

if [[ "${OSMO_VALIDATE_KAI_BIND_LOG}" == "true" ]]; then
  KAI_SCHEDULER_NAMESPACE="${KAI_SCHEDULER_NAMESPACE:-$(version_value kai_scheduler_namespace)}"
  KAI_BINDER_DEPLOYMENT="${KAI_BINDER_DEPLOYMENT:-binder}"
  if ! kubectl -n "${KAI_SCHEDULER_NAMESPACE}" logs "deployment/${KAI_BINDER_DEPLOYMENT}" \
    --since="${OSMO_KAI_BIND_LOG_SINCE}" | grep -Eq 'Binding pod to node|Pod bound successfully'; then
    die "KAI binder logs do not show a recent successful bind"
  fi
fi

if kubectl get pods -n "${OSMO_NAMESPACE}" -o json | jq -e '
  .items[]
  | select(any(.status.containerStatuses[]?; .state.waiting.reason == "CrashLoopBackOff"))
' >/dev/null; then
  kubectl get pods -n "${OSMO_NAMESPACE}" >&2
  die "one or more OSMO pods are in CrashLoopBackOff"
fi

BACKEND_TOKEN_BYTES="$(kubectl -n "${OSMO_NAMESPACE}" get secret backend-operator-token -o jsonpath='{.data.token}' | base64_decode | wc -c | tr -d ' ')"
[[ "${BACKEND_TOKEN_BYTES}" -gt 0 ]] || die "backend operator token secret is empty"

SERVICE_POD_MONITOR="$(helm get values osmo-service --namespace "${OSMO_NAMESPACE}" -o json | jq -r '.podMonitor.enabled // false')"
BACKEND_POD_MONITOR="$(helm get values osmo-backend --namespace "${OSMO_NAMESPACE}" -o json | jq -r '.podMonitor.enabled // false')"

if [[ "${SERVICE_POD_MONITOR}" == "true" || "${BACKEND_POD_MONITOR}" == "true" ]]; then
  kubectl get crd podmonitors.monitoring.coreos.com >/dev/null || die "PodMonitor is enabled without Prometheus Operator CRDs"
fi

log "platform validation passed"
