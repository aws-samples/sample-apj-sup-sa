#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=../../scripts/common.sh
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/common.sh"

require_cmds kubectl jq terraform

KARPENTER_NODEPOOL_NAME="${KARPENTER_NODEPOOL_NAME:-$(version_value karpenter_nodepool_name)}"
GPU_OPERATOR_NAMESPACE="${GPU_OPERATOR_NAMESPACE:-$(version_value gpu_operator_namespace)}"
GPU_CLEANUP_DELETE_PREWARM="${GPU_CLEANUP_DELETE_PREWARM:-true}"
GPU_CLEANUP_DELETE_COMPLETED_VALIDATORS="${GPU_CLEANUP_DELETE_COMPLETED_VALIDATORS:-true}"
GPU_CLEANUP_DELETE_WORKFLOW_PODS="${GPU_CLEANUP_DELETE_WORKFLOW_PODS:-true}"
GPU_CLEANUP_DELETE_EMPTY_NODECLAIMS="${GPU_CLEANUP_DELETE_EMPTY_NODECLAIMS:-true}"
GPU_CLEANUP_PREWARM_POD_NAME="${GPU_CLEANUP_PREWARM_POD_NAME:-aws-osmo-gpu-prewarm}"
GPU_CLEANUP_TIMEOUT_SECONDS="${GPU_CLEANUP_TIMEOUT_SECONDS:-1800}"
GPU_CLEANUP_POLL_SECONDS="${GPU_CLEANUP_POLL_SECONDS:-15}"

configure_kubectl
OSMO_WORKLOAD_NAMESPACE="$(terraform_output osmo_workload_namespace)"

if [[ "${GPU_CLEANUP_DELETE_PREWARM}" == "true" ]]; then
  kubectl -n "${OSMO_WORKLOAD_NAMESPACE}" delete pod "${GPU_CLEANUP_PREWARM_POD_NAME}" \
    --ignore-not-found >/dev/null
fi

if [[ "${GPU_CLEANUP_DELETE_COMPLETED_VALIDATORS}" == "true" ]]; then
  kubectl -n "${GPU_OPERATOR_NAMESPACE}" delete pod \
    -l app=nvidia-cuda-validator \
    --field-selector=status.phase=Succeeded \
    --ignore-not-found >/dev/null
fi

node_has_blocking_pods() {
  local node_name="$1"
  kubectl get pods -A \
    --field-selector "spec.nodeName=${node_name}" \
    -o json | jq -e '
      any(.items[];
        ((.metadata.deletionTimestamp // "") == "") and
        (.status.phase != "Succeeded" and .status.phase != "Failed") and
        (((.metadata.ownerReferences // []) | map(.kind) | index("DaemonSet")) == null) and
        ((.metadata.annotations["kubernetes.io/config.mirror"] // "") == "")
      )
    ' >/dev/null
}

delete_residual_workflow_pods() {
  local node_name="$1"
  kubectl -n "${OSMO_WORKLOAD_NAMESPACE}" get pods \
    --field-selector "spec.nodeName=${node_name}" \
    -o json | jq -r --arg prewarm "${GPU_CLEANUP_PREWARM_POD_NAME}" '
      .items[] |
      select(.metadata.name != $prewarm) |
      select((.metadata.deletionTimestamp // "") == "") |
      .metadata.name
    ' |
    while IFS= read -r pod_name; do
      [[ -n "${pod_name}" ]] || continue
      log "deleting residual OSMO workflow pod ${OSMO_WORKLOAD_NAMESPACE}/${pod_name}"
      kubectl -n "${OSMO_WORKLOAD_NAMESPACE}" delete pod "${pod_name}" \
        --wait=false --ignore-not-found >/dev/null || true
    done
}

delete_empty_nodeclaims() {
  kubectl get nodeclaim -l "karpenter.sh/nodepool=${KARPENTER_NODEPOOL_NAME}" -o json |
    jq -r '.items[] | [.metadata.name, (.status.nodeName // ""), (.metadata.deletionTimestamp // "")] | @tsv' |
    while IFS=$'\t' read -r nodeclaim_name node_name deletion_timestamp; do
      [[ -n "${nodeclaim_name}" ]] || continue
      [[ -z "${deletion_timestamp}" ]] || continue
      if [[ -n "${node_name}" && "${GPU_CLEANUP_DELETE_WORKFLOW_PODS}" == "true" ]]; then
        delete_residual_workflow_pods "${node_name}"
      fi
      if [[ -n "${node_name}" ]] && node_has_blocking_pods "${node_name}"; then
        log "waiting for GPU workload pods to leave ${node_name}"
        continue
      fi

      log "deleting empty GPU NodeClaim ${nodeclaim_name}"
      kubectl delete nodeclaim "${nodeclaim_name}" --wait=false >/dev/null || true
    done
}

deadline="$(( $(date -u +%s) + GPU_CLEANUP_TIMEOUT_SECONDS ))"
attempt=0

while [[ "$(date -u +%s)" -lt "${deadline}" ]]; do
  nodeclaim_count="$(kubectl get nodeclaim -l "karpenter.sh/nodepool=${KARPENTER_NODEPOOL_NAME}" \
    --no-headers 2>/dev/null | wc -l | tr -d ' ')"
  node_count="$(kubectl get nodes -l "karpenter.sh/nodepool=${KARPENTER_NODEPOOL_NAME}" \
    --no-headers 2>/dev/null | wc -l | tr -d ' ')"

  if [[ "${nodeclaim_count}" == "0" && "${node_count}" == "0" ]]; then
    log "Karpenter GPU nodes cleaned up"
    exit 0
  fi

  if [[ "${GPU_CLEANUP_DELETE_EMPTY_NODECLAIMS}" == "true" ]]; then
    delete_empty_nodeclaims
  fi

  attempt="$((attempt + 1))"
  if (( attempt % 4 == 0 )); then
    log "waiting for GPU cleanup: ${nodeclaim_count} NodeClaims, ${node_count} Nodes remain"
  fi
  sleep "${GPU_CLEANUP_POLL_SECONDS}"
done

log "GPU cleanup did not complete before timeout"
kubectl get nodeclaim -l "karpenter.sh/nodepool=${KARPENTER_NODEPOOL_NAME}" -o wide >&2 || true
kubectl get nodes -l "karpenter.sh/nodepool=${KARPENTER_NODEPOOL_NAME}" \
  -L node.kubernetes.io/instance-type,nvidia.com/gpu.count,nvidia.com/gpu.product >&2 || true

while IFS= read -r node_name; do
  [[ -n "${node_name}" ]] || continue
  log "pods still associated with ${node_name}"
  kubectl get pods -A -o wide --field-selector "spec.nodeName=${node_name}" >&2 || true
done < <(kubectl get nodes -l "karpenter.sh/nodepool=${KARPENTER_NODEPOOL_NAME}" \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null)

kubectl get events -A --sort-by=.lastTimestamp | tail -80 >&2 || true
kubectl -n "$(version_value karpenter_namespace)" logs "deployment/$(version_value karpenter_release_name)" \
  --since=30m --tail=200 >&2 || true

die "Karpenter GPU nodes were not cleaned up"
