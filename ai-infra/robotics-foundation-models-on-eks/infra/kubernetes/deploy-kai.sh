#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=../../scripts/common.sh
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/common.sh"

require_cmds aws kubectl helm terraform

KAI_SCHEDULER_CHART="${KAI_SCHEDULER_CHART:-$(version_value kai_scheduler_chart)}"
KAI_SCHEDULER_CHART_VERSION="${KAI_SCHEDULER_CHART_VERSION:-$(version_value kai_scheduler_chart_version)}"
KAI_SCHEDULER_NAMESPACE="${KAI_SCHEDULER_NAMESPACE:-$(version_value kai_scheduler_namespace)}"
KAI_SCHEDULER_RELEASE_NAME="${KAI_SCHEDULER_RELEASE_NAME:-$(version_value kai_scheduler_release_name)}"
KAI_SCHEDULER_DEPLOYMENT="${KAI_SCHEDULER_DEPLOYMENT:-kai-scheduler-default}"
KAI_EXPECTED_DEPLOYMENTS="${KAI_EXPECTED_DEPLOYMENTS:-kai-operator admission binder pod-grouper podgroup-controller queue-controller kai-scheduler-default}"
KAI_GPU_POD_RUNTIME_CLASS_NAME="${KAI_GPU_POD_RUNTIME_CLASS_NAME:-}"

KAI_VALUES="$(mktemp)"
trap 'rm -f "${KAI_VALUES}"' EXIT

wait_for_deployment() {
  local namespace="$1"
  local deployment="$2"

  for _ in $(seq 1 90); do
    if kubectl -n "${namespace}" get deployment "${deployment}" >/dev/null 2>&1; then
      kubectl -n "${namespace}" rollout status "deployment/${deployment}" --timeout=10m
      return 0
    fi
    sleep 2
  done

  die "KAI deployment did not appear: ${namespace}/${deployment}"
}

configure_kubectl

log "deploying KAI Scheduler ${KAI_SCHEDULER_CHART_VERSION}"

if [[ -n "${KAI_GPU_POD_RUNTIME_CLASS_NAME}" ]]; then
  cat >"${KAI_VALUES}" <<EOF
admission:
  gpuPodRuntimeClassName: "${KAI_GPU_POD_RUNTIME_CLASS_NAME}"
EOF
else
  cat >"${KAI_VALUES}" <<'EOF'
admission:
  gpuPodRuntimeClassName: ""
EOF
fi

if [[ "$(kubectl get crd podgroups.scheduling.run.ai \
  -o jsonpath='{.metadata.labels.app\.kubernetes\.io/name}' 2>/dev/null || true)" == "osmo-podgroup-compat" ]]; then
  log "removing temporary OSMO PodGroup compatibility CRD before installing KAI"
  kubectl delete crd podgroups.scheduling.run.ai --wait=true
fi

helm upgrade --install "${KAI_SCHEDULER_RELEASE_NAME}" "${KAI_SCHEDULER_CHART}" \
  --namespace "${KAI_SCHEDULER_NAMESPACE}" \
  --create-namespace \
  --version "${KAI_SCHEDULER_CHART_VERSION}" \
  --values "${KAI_VALUES}" \
  --wait \
  --timeout 15m

kubectl wait --for=condition=Established crd/configs.kai.scheduler --timeout=5m
kubectl wait --for=condition=Established crd/schedulingshards.kai.scheduler --timeout=5m
kubectl wait --for=condition=Established crd/podgroups.scheduling.run.ai --timeout=5m
kubectl wait --for=condition=Established crd/queues.scheduling.run.ai --timeout=5m

for deployment in ${KAI_EXPECTED_DEPLOYMENTS}; do
  wait_for_deployment "${KAI_SCHEDULER_NAMESPACE}" "${deployment}"
done

# KAI's operator may reconcile the scheduler ServiceAccount after Helm starts
# the scheduler during upgrades. Restart once so the projected token is current.
kubectl -n "${KAI_SCHEDULER_NAMESPACE}" rollout restart "deployment/${KAI_SCHEDULER_DEPLOYMENT}"
wait_for_deployment "${KAI_SCHEDULER_NAMESPACE}" "${KAI_SCHEDULER_DEPLOYMENT}"

kubectl get queue default-queue >/dev/null

log "KAI Scheduler deployment completed"
