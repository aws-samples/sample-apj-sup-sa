#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=./scripts/common.sh
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

require_cmds aws jq terraform

empty_versioned_bucket() {
  local bucket="$1"
  local region="$2"
  local versions_json delete_json object_count

  while true; do
    versions_json="$(aws s3api list-object-versions \
      --bucket "${bucket}" \
      --region "${region}" \
      --output json 2>/dev/null || true)"
    [[ -n "${versions_json}" ]] || return 0

    delete_json="$(printf '%s' "${versions_json}" | jq -c '{
      Objects: (((.Versions // []) + (.DeleteMarkers // [])) | map({Key: .Key, VersionId: .VersionId})),
      Quiet: true
    }')"
    object_count="$(printf '%s' "${delete_json}" | jq '.Objects | length')"
    [[ "${object_count}" -gt 0 ]] || return 0

    aws s3api delete-objects \
      --bucket "${bucket}" \
      --region "${region}" \
      --delete "${delete_json}" >/dev/null
  done
}

terraform_output_clean() {
  local key="$1"
  local value
  value="$(terraform -chdir="${TF_DIR}" output -raw "${key}" 2>/dev/null || true)"
  [[ -n "${value}" ]] || return 1
  if printf '%s' "${value}" | grep -Eq 'No outputs found|Warning:'; then
    return 1
  fi
  printf '%s' "${value}"
}

s3_bucket_from_state() {
  terraform -chdir="${TF_DIR}" state show -no-color aws_s3_bucket.osmo 2>/dev/null |
    awk -F' = ' '$1 ~ /^[[:space:]]*bucket[[:space:]]*$/ {gsub(/"/, "", $2); print $2; exit}'
}

s3_bucket_region_from_state() {
  terraform -chdir="${TF_DIR}" state show -no-color aws_s3_bucket.osmo 2>/dev/null |
    awk -F' = ' '$1 ~ /^[[:space:]]*region[[:space:]]*$/ {gsub(/"/, "", $2); print $2; exit}'
}

if [[ "${SKIP_OSMO_UNINSTALL:-false}" != "true" ]] && terraform_output_clean cluster_name >/dev/null; then
  if command -v kubectl >/dev/null 2>&1 && command -v helm >/dev/null 2>&1; then
    configure_kubectl || true
    OSMO_NAMESPACE="$(terraform_output osmo_namespace 2>/dev/null || printf 'osmo')"
    KAI_SCHEDULER_NAMESPACE="$(version_value kai_scheduler_namespace 2>/dev/null || printf 'kai-scheduler')"
    KAI_SCHEDULER_RELEASE_NAME="$(version_value kai_scheduler_release_name 2>/dev/null || printf 'kai-scheduler')"
    KARPENTER_NAMESPACE="$(version_value karpenter_namespace 2>/dev/null || printf 'kube-system')"
    KARPENTER_RELEASE_NAME="$(version_value karpenter_release_name 2>/dev/null || printf 'karpenter')"
    KARPENTER_NODEPOOL_NAME="$(version_value karpenter_nodepool_name 2>/dev/null || printf 'aws-osmo-g7e')"
    KARPENTER_EC2NODECLASS_NAME="$(version_value karpenter_ec2nodeclass_name 2>/dev/null || printf 'aws-osmo-g7e')"
    KARPENTER_G6E_NODEPOOL_NAME="$(version_value karpenter_g6e_nodepool_name 2>/dev/null || printf 'aws-osmo-g6e')"
    KARPENTER_G6E_EC2NODECLASS_NAME="$(version_value karpenter_g6e_ec2nodeclass_name 2>/dev/null || printf 'aws-osmo-g6e')"
    GPU_OPERATOR_NAMESPACE="$(version_value gpu_operator_namespace 2>/dev/null || printf 'gpu-operator')"
    GPU_OPERATOR_RELEASE_NAME="$(version_value gpu_operator_release_name 2>/dev/null || printf 'gpu-operator')"
    EFA_DEVICE_PLUGIN_NAMESPACE="$(version_value efa_device_plugin_namespace 2>/dev/null || printf 'kube-system')"
    EFA_DEVICE_PLUGIN_RELEASE_NAME="$(version_value efa_device_plugin_release_name 2>/dev/null || printf 'aws-efa-k8s-device-plugin')"
    helm uninstall osmo-ui --namespace "${OSMO_NAMESPACE}" >/dev/null 2>&1 || true
    helm uninstall osmo-backend --namespace "${OSMO_NAMESPACE}" >/dev/null 2>&1 || true
    helm uninstall osmo-service --namespace "${OSMO_NAMESPACE}" >/dev/null 2>&1 || true
    helm uninstall osmo-router --namespace "${OSMO_NAMESPACE}" >/dev/null 2>&1 || true
    kubectl -n "${OSMO_NAMESPACE}" delete deployment,service,configmap \
      -l app.kubernetes.io/name=osmo-internal-router >/dev/null 2>&1 || true
    if [[ "${SKIP_KAI_UNINSTALL:-false}" != "true" ]]; then
      helm uninstall "${KAI_SCHEDULER_RELEASE_NAME}" --namespace "${KAI_SCHEDULER_NAMESPACE}" >/dev/null 2>&1 || true
    fi
    if [[ "${SKIP_KARPENTER_UNINSTALL:-false}" != "true" ]]; then
      kubectl delete nodepool "${KARPENTER_NODEPOOL_NAME}" --ignore-not-found >/dev/null 2>&1 || true
      kubectl delete nodepool "${KARPENTER_G6E_NODEPOOL_NAME}" --ignore-not-found >/dev/null 2>&1 || true
      kubectl delete ec2nodeclass "${KARPENTER_EC2NODECLASS_NAME}" --ignore-not-found >/dev/null 2>&1 || true
      kubectl delete ec2nodeclass "${KARPENTER_G6E_EC2NODECLASS_NAME}" --ignore-not-found >/dev/null 2>&1 || true
      if kubectl get crd nodeclaims.karpenter.sh >/dev/null 2>&1; then
        kubectl delete nodeclaim --all --timeout=20m >/dev/null 2>&1 || true
      fi
      helm uninstall "${KARPENTER_RELEASE_NAME}" --namespace "${KARPENTER_NAMESPACE}" >/dev/null 2>&1 || true
    fi
    if [[ "${SKIP_GPU_OPERATOR_UNINSTALL:-false}" != "true" ]]; then
      helm uninstall "${GPU_OPERATOR_RELEASE_NAME}" --namespace "${GPU_OPERATOR_NAMESPACE}" >/dev/null 2>&1 || true
    fi
    if [[ "${SKIP_EFA_DEVICE_PLUGIN_UNINSTALL:-false}" != "true" ]]; then
      helm uninstall "${EFA_DEVICE_PLUGIN_RELEASE_NAME}" --namespace "${EFA_DEVICE_PLUGIN_NAMESPACE}" >/dev/null 2>&1 || true
    fi
  fi
fi

if [[ "${EMPTY_ARTIFACT_BUCKET_BEFORE_DESTROY:-true}" == "true" ]] &&
  terraform -chdir="${TF_DIR}" state list 2>/dev/null | grep -qx 'aws_s3_bucket.osmo'; then
  AWS_REGION="$(terraform_output_clean aws_region || s3_bucket_region_from_state || printf '%s' "${AWS_REGION:-us-west-2}")"
  OSMO_ARTIFACTS_BUCKET="$(terraform_output_clean osmo_artifacts_bucket || s3_bucket_from_state)"
  [[ -n "${OSMO_ARTIFACTS_BUCKET}" ]] || die "could not determine artifact bucket name from Terraform state"
  log "emptying versioned artifact bucket before destroy: ${OSMO_ARTIFACTS_BUCKET}"
  empty_versioned_bucket "${OSMO_ARTIFACTS_BUCKET}" "${AWS_REGION}"
fi

terraform -chdir="${TF_DIR}" destroy "$@"
