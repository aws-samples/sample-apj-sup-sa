#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=../../scripts/common.sh
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/common.sh"

require_cmds aws kubectl helm jq terraform

KARPENTER_CHART="${KARPENTER_CHART:-$(version_value karpenter_chart)}"
KARPENTER_CHART_VERSION="${KARPENTER_CHART_VERSION:-$(version_value karpenter_chart_version)}"
KARPENTER_NAMESPACE="${KARPENTER_NAMESPACE:-$(version_value karpenter_namespace)}"
KARPENTER_RELEASE_NAME="${KARPENTER_RELEASE_NAME:-$(version_value karpenter_release_name)}"
KARPENTER_NODEPOOL_NAME="${KARPENTER_NODEPOOL_NAME:-$(version_value karpenter_nodepool_name)}"
KARPENTER_EC2NODECLASS_NAME="${KARPENTER_EC2NODECLASS_NAME:-$(version_value karpenter_ec2nodeclass_name)}"
KARPENTER_G6E_NODEPOOL_NAME="${KARPENTER_G6E_NODEPOOL_NAME:-$(version_value karpenter_g6e_nodepool_name)}"
KARPENTER_G6E_EC2NODECLASS_NAME="${KARPENTER_G6E_EC2NODECLASS_NAME:-$(version_value karpenter_g6e_ec2nodeclass_name)}"
KARPENTER_G7E_INSTANCE_TYPES="${KARPENTER_G7E_INSTANCE_TYPES:-$(version_value g7e_nut_pouring_instance_types)}"
KARPENTER_G6E_INSTANCE_TYPES="${KARPENTER_G6E_INSTANCE_TYPES:-$(version_value g6e_gr00t_instance_types)}"
KARPENTER_DEPLOY_G6E_NODEPOOL="${KARPENTER_DEPLOY_G6E_NODEPOOL:-true}"
EKS_AL2023_NVIDIA_AMI_RELEASE="${EKS_AL2023_NVIDIA_AMI_RELEASE:-$(version_value eks_al2023_nvidia_ami_release)}"
KARPENTER_NODE_ROOT_VOLUME_SIZE="${KARPENTER_NODE_ROOT_VOLUME_SIZE:-1024Gi}"
KARPENTER_NODEPOOL_CPU_LIMIT="${KARPENTER_NODEPOOL_CPU_LIMIT:-384}"
KARPENTER_NODEPOOL_MEMORY_LIMIT="${KARPENTER_NODEPOOL_MEMORY_LIMIT:-5000Gi}"
KARPENTER_NODE_EXPIRE_AFTER="${KARPENTER_NODE_EXPIRE_AFTER:-Never}"
KARPENTER_CONSOLIDATION_POLICY="${KARPENTER_CONSOLIDATION_POLICY:-WhenEmpty}"
KARPENTER_CONSOLIDATE_AFTER="${KARPENTER_CONSOLIDATE_AFTER:-24h}"
KARPENTER_CAPACITY_TYPES="${KARPENTER_CAPACITY_TYPES:-on-demand}"
KARPENTER_CAPACITY_RESERVATION_IDS="${KARPENTER_CAPACITY_RESERVATION_IDS:-${KARPENTER_CAPACITY_RESERVATION_ID:-}}"

comma_values_to_yaml() {
  local csv="$1"
  local value
  tr ',' '\n' <<<"${csv}" | while IFS= read -r value; do
    value="$(printf '%s' "${value}" | xargs)"
    [[ -n "${value}" ]] || continue
    printf '              - "%s"\n' "${value}"
  done
}

capacity_reservation_ids_to_yaml() {
  local csv="$1"
  local value

  [[ -n "$(printf '%s' "${csv}" | tr -d '[:space:],')" ]] || return 0

  printf '  capacityReservationSelectorTerms:\n'
  tr ',' '\n' <<<"${csv}" | while IFS= read -r value; do
    value="$(printf '%s' "${value}" | xargs)"
    [[ -n "${value}" ]] || continue
    printf '    - id: "%s"\n' "${value}"
  done
}

configure_kubectl

AWS_REGION="$(terraform_output aws_region)"
CLUSTER_NAME="$(terraform_output cluster_name)"
CLUSTER_ENDPOINT="$(terraform_output cluster_endpoint)"
KARPENTER_QUEUE_NAME="$(terraform_output karpenter_interruption_queue_name)"
KARPENTER_NODE_IAM_ROLE_NAME="$(terraform_output karpenter_node_iam_role_name)"
K8S_VERSION="$(version_value eks_cluster_version)"
AMI_SSM_PARAMETER="/aws/service/eks/optimized-ami/${K8S_VERSION}/amazon-linux-2023/x86_64/nvidia/${EKS_AL2023_NVIDIA_AMI_RELEASE}/image_id"

log "deploying Karpenter ${KARPENTER_CHART_VERSION}"
helm registry logout public.ecr.aws >/dev/null 2>&1 || true
helm upgrade --install "${KARPENTER_RELEASE_NAME}" "${KARPENTER_CHART}" \
  --namespace "${KARPENTER_NAMESPACE}" \
  --create-namespace \
  --version "${KARPENTER_CHART_VERSION}" \
  --set "settings.clusterName=${CLUSTER_NAME}" \
  --set "settings.clusterEndpoint=${CLUSTER_ENDPOINT}" \
  --set "settings.interruptionQueue=${KARPENTER_QUEUE_NAME}" \
  --set controller.resources.requests.cpu=1 \
  --set controller.resources.requests.memory=1Gi \
  --set controller.resources.limits.cpu=1 \
  --set controller.resources.limits.memory=1Gi \
  --wait \
  --timeout 15m

kubectl -n "${KARPENTER_NAMESPACE}" rollout status deployment/"${KARPENTER_RELEASE_NAME}" --timeout=10m
kubectl wait --for=condition=Established crd/nodepools.karpenter.sh --timeout=5m
kubectl wait --for=condition=Established crd/nodeclaims.karpenter.sh --timeout=5m
kubectl wait --for=condition=Established crd/ec2nodeclasses.karpenter.k8s.aws --timeout=5m

if ! aws ssm get-parameter --region "${AWS_REGION}" --name "${AMI_SSM_PARAMETER}" >/dev/null; then
  die "pinned EKS AL2023 NVIDIA AMI SSM parameter was not found: ${AMI_SSM_PARAMETER}"
fi

G7E_INSTANCE_TYPE_VALUES="$(comma_values_to_yaml "${KARPENTER_G7E_INSTANCE_TYPES}")"
[[ -n "${G7E_INSTANCE_TYPE_VALUES}" ]] || die "KARPENTER_G7E_INSTANCE_TYPES must contain at least one instance type"
if [[ "${KARPENTER_DEPLOY_G6E_NODEPOOL}" == "true" ]]; then
  G6E_INSTANCE_TYPE_VALUES="$(comma_values_to_yaml "${KARPENTER_G6E_INSTANCE_TYPES}")"
  [[ -n "${G6E_INSTANCE_TYPE_VALUES}" ]] || die "KARPENTER_G6E_INSTANCE_TYPES must contain at least one instance type"
else
  G6E_INSTANCE_TYPE_VALUES=""
fi
CAPACITY_TYPE_VALUES="$(comma_values_to_yaml "${KARPENTER_CAPACITY_TYPES}")"
[[ -n "${CAPACITY_TYPE_VALUES}" ]] || die "KARPENTER_CAPACITY_TYPES must contain at least one capacity type"
CAPACITY_RESERVATION_SELECTOR_TERMS="$(capacity_reservation_ids_to_yaml "${KARPENTER_CAPACITY_RESERVATION_IDS}")"
if [[ -n "${CAPACITY_RESERVATION_SELECTOR_TERMS}" ]]; then
  log "restricting G7e EC2NodeClass to capacity reservation IDs: ${KARPENTER_CAPACITY_RESERVATION_IDS}"
fi

log "applying G7e EC2NodeClass and NodePool"
kubectl apply -f - <<YAML
apiVersion: karpenter.k8s.aws/v1
kind: EC2NodeClass
metadata:
  name: ${KARPENTER_EC2NODECLASS_NAME}
  labels:
    app.kubernetes.io/name: karpenter
    app.kubernetes.io/part-of: aws-osmo-reference
spec:
  amiFamily: AL2023
  role: "${KARPENTER_NODE_IAM_ROLE_NAME}"
  amiSelectorTerms:
    - ssmParameter: "${AMI_SSM_PARAMETER}"
  subnetSelectorTerms:
    - tags:
        karpenter.sh/discovery: "${CLUSTER_NAME}"
  securityGroupSelectorTerms:
    - tags:
        karpenter.sh/discovery: "${CLUSTER_NAME}"
${CAPACITY_RESERVATION_SELECTOR_TERMS}
  metadataOptions:
    httpEndpoint: enabled
    httpProtocolIPv6: disabled
    httpPutResponseHopLimit: 1
    httpTokens: required
  blockDeviceMappings:
    - deviceName: /dev/xvda
      ebs:
        volumeSize: ${KARPENTER_NODE_ROOT_VOLUME_SIZE}
        volumeType: gp3
        encrypted: true
        deleteOnTermination: true
  tags:
    Project: aws-osmo
    Reference: aws-osmo
    ManagedBy: karpenter
---
apiVersion: karpenter.sh/v1
kind: NodePool
metadata:
  name: ${KARPENTER_NODEPOOL_NAME}
  labels:
    app.kubernetes.io/name: karpenter
    app.kubernetes.io/part-of: aws-osmo-reference
spec:
  template:
    metadata:
      labels:
        aws.osmo.reference/nodepool: g7e
        aws.osmo.reference/gpu-family: rtx-pro-6000
    spec:
      nodeClassRef:
        group: karpenter.k8s.aws
        kind: EC2NodeClass
        name: ${KARPENTER_EC2NODECLASS_NAME}
      taints:
        - key: nvidia.com/gpu
          value: "true"
          effect: NoSchedule
      requirements:
        - key: kubernetes.io/arch
          operator: In
          values: ["amd64"]
        - key: kubernetes.io/os
          operator: In
          values: ["linux"]
        - key: karpenter.sh/capacity-type
          operator: In
          values:
${CAPACITY_TYPE_VALUES}
        - key: node.kubernetes.io/instance-type
          operator: In
          values:
${G7E_INSTANCE_TYPE_VALUES}
      expireAfter: ${KARPENTER_NODE_EXPIRE_AFTER}
  limits:
    cpu: "${KARPENTER_NODEPOOL_CPU_LIMIT}"
    memory: "${KARPENTER_NODEPOOL_MEMORY_LIMIT}"
  disruption:
    consolidationPolicy: ${KARPENTER_CONSOLIDATION_POLICY}
    consolidateAfter: ${KARPENTER_CONSOLIDATE_AFTER}
YAML

if [[ "${KARPENTER_DEPLOY_G6E_NODEPOOL}" == "true" ]]; then
  log "applying G6e EC2NodeClass and NodePool"
  kubectl apply -f - <<YAML
apiVersion: karpenter.k8s.aws/v1
kind: EC2NodeClass
metadata:
  name: ${KARPENTER_G6E_EC2NODECLASS_NAME}
  labels:
    app.kubernetes.io/name: karpenter
    app.kubernetes.io/part-of: aws-osmo-reference
spec:
  amiFamily: AL2023
  role: "${KARPENTER_NODE_IAM_ROLE_NAME}"
  amiSelectorTerms:
    - ssmParameter: "${AMI_SSM_PARAMETER}"
  subnetSelectorTerms:
    - tags:
        karpenter.sh/discovery: "${CLUSTER_NAME}"
  securityGroupSelectorTerms:
    - tags:
        karpenter.sh/discovery: "${CLUSTER_NAME}"
  metadataOptions:
    httpEndpoint: enabled
    httpProtocolIPv6: disabled
    httpPutResponseHopLimit: 1
    httpTokens: required
  blockDeviceMappings:
    - deviceName: /dev/xvda
      ebs:
        volumeSize: ${KARPENTER_NODE_ROOT_VOLUME_SIZE}
        volumeType: gp3
        encrypted: true
        deleteOnTermination: true
  tags:
    Project: aws-osmo
    Reference: aws-osmo
    ManagedBy: karpenter
---
apiVersion: karpenter.sh/v1
kind: NodePool
metadata:
  name: ${KARPENTER_G6E_NODEPOOL_NAME}
  labels:
    app.kubernetes.io/name: karpenter
    app.kubernetes.io/part-of: aws-osmo-reference
spec:
  template:
    metadata:
      labels:
        aws.osmo.reference/nodepool: g6e
        aws.osmo.reference/gpu-family: l40s
    spec:
      nodeClassRef:
        group: karpenter.k8s.aws
        kind: EC2NodeClass
        name: ${KARPENTER_G6E_EC2NODECLASS_NAME}
      taints:
        - key: nvidia.com/gpu
          value: "true"
          effect: NoSchedule
      requirements:
        - key: kubernetes.io/arch
          operator: In
          values: ["amd64"]
        - key: kubernetes.io/os
          operator: In
          values: ["linux"]
        - key: karpenter.sh/capacity-type
          operator: In
          values:
${CAPACITY_TYPE_VALUES}
        - key: node.kubernetes.io/instance-type
          operator: In
          values:
${G6E_INSTANCE_TYPE_VALUES}
      expireAfter: ${KARPENTER_NODE_EXPIRE_AFTER}
  limits:
    cpu: "${KARPENTER_NODEPOOL_CPU_LIMIT}"
    memory: "${KARPENTER_NODEPOOL_MEMORY_LIMIT}"
  disruption:
    consolidationPolicy: ${KARPENTER_CONSOLIDATION_POLICY}
    consolidateAfter: ${KARPENTER_CONSOLIDATE_AFTER}
YAML
fi

kubectl wait --for=condition=Ready "ec2nodeclass/${KARPENTER_EC2NODECLASS_NAME}" --timeout=10m
kubectl wait --for=condition=Ready "nodepool/${KARPENTER_NODEPOOL_NAME}" --timeout=10m
if [[ "${KARPENTER_DEPLOY_G6E_NODEPOOL}" == "true" ]]; then
  kubectl wait --for=condition=Ready "ec2nodeclass/${KARPENTER_G6E_EC2NODECLASS_NAME}" --timeout=10m
  kubectl wait --for=condition=Ready "nodepool/${KARPENTER_G6E_NODEPOOL_NAME}" --timeout=10m
fi

log "Karpenter GPU NodePool deployment completed"
