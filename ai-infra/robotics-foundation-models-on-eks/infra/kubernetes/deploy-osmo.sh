#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=../../scripts/common.sh
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/common.sh"

require_cmds aws kubectl helm jq openssl terraform osmo

OSMO_VERSION="${OSMO_VERSION:-$(version_value release)}"
OSMO_CHART_REPO="${OSMO_CHART_REPO:-$(version_value chart_repository)}"
OSMO_CHART_VERSION="${OSMO_CHART_VERSION:-$(version_value chart_version)}"
OSMO_IMAGE_REGISTRY="${OSMO_IMAGE_REGISTRY:-$(version_value image_registry)}"
OSMO_IMAGE_TAG="${OSMO_IMAGE_TAG:-$(version_value image_tag)}"
OSMO_BACKEND_NAME="${OSMO_BACKEND_NAME:-default}"
OSMO_CONFIGURE_GPU_PLATFORM="${OSMO_CONFIGURE_GPU_PLATFORM:-true}"
OSMO_GPU_PLATFORM_NAME="${OSMO_GPU_PLATFORM_NAME:-g7e-rtx-pro-6000}"
OSMO_GPU_POD_TEMPLATE_NAME="${OSMO_GPU_POD_TEMPLATE_NAME:-aws-g7e-rtx-pro-6000}"
OSMO_GPU_PLATFORM_LABEL_KEY="${OSMO_GPU_PLATFORM_LABEL_KEY:-karpenter.sh/nodepool}"
OSMO_GPU_PLATFORM_LABEL_VALUE="${OSMO_GPU_PLATFORM_LABEL_VALUE:-$(version_value karpenter_nodepool_name)}"
OSMO_G6E_GPU_PLATFORM_NAME="${OSMO_G6E_GPU_PLATFORM_NAME:-g6e-l40s}"
OSMO_G6E_GPU_POD_TEMPLATE_NAME="${OSMO_G6E_GPU_POD_TEMPLATE_NAME:-aws-g6e-l40s}"
OSMO_G6E_GPU_PLATFORM_LABEL_VALUE="${OSMO_G6E_GPU_PLATFORM_LABEL_VALUE:-$(version_value karpenter_g6e_nodepool_name)}"
OSMO_GPU_POD_DO_NOT_DISRUPT="${OSMO_GPU_POD_DO_NOT_DISRUPT:-true}"
OSMO_GPU_POD_SHM_SIZE="${OSMO_GPU_POD_SHM_SIZE:-32Gi}"
OSMO_CONFIGURE_EFA_PLATFORMS="${OSMO_CONFIGURE_EFA_PLATFORMS:-true}"
OSMO_EFA_RESOURCE_NAME="${OSMO_EFA_RESOURCE_NAME:-vpc.amazonaws.com/efa}"
OSMO_EFA_RESOURCE_COUNT="${OSMO_EFA_RESOURCE_COUNT:-1}"
OSMO_G7E_EFA_PLATFORM_NAME="${OSMO_G7E_EFA_PLATFORM_NAME:-g7e-rtx-pro-6000-efa}"
OSMO_G7E_EFA_POD_TEMPLATE_NAME="${OSMO_G7E_EFA_POD_TEMPLATE_NAME:-aws-g7e-rtx-pro-6000-efa}"
OSMO_G7E_EFA_PLATFORM_LABEL_VALUE="${OSMO_G7E_EFA_PLATFORM_LABEL_VALUE:-$(version_value karpenter_nodepool_name)}"
OSMO_G6E_EFA_PLATFORM_NAME="${OSMO_G6E_EFA_PLATFORM_NAME:-g6e-l40s-efa}"
OSMO_G6E_EFA_POD_TEMPLATE_NAME="${OSMO_G6E_EFA_POD_TEMPLATE_NAME:-aws-g6e-l40s-efa}"
OSMO_G6E_EFA_PLATFORM_LABEL_VALUE="${OSMO_G6E_EFA_PLATFORM_LABEL_VALUE:-$(version_value karpenter_g6e_nodepool_name)}"
OSMO_INSTALL_KAI="${OSMO_INSTALL_KAI:-true}"
if [[ "${OSMO_INSTALL_KAI}" == "true" ]]; then
  OSMO_K8S_SCHEDULER_NAME="${OSMO_K8S_SCHEDULER_NAME:-$(version_value kai_scheduler_name)}"
  OSMO_INSTALL_PODGROUP_COMPAT_CRD="${OSMO_INSTALL_PODGROUP_COMPAT_CRD:-false}"
else
  OSMO_K8S_SCHEDULER_NAME="${OSMO_K8S_SCHEDULER_NAME:-default-scheduler}"
  OSMO_INSTALL_PODGROUP_COMPAT_CRD="${OSMO_INSTALL_PODGROUP_COMPAT_CRD:-true}"
fi
OSMO_DEPLOY_WEB_UI="${OSMO_DEPLOY_WEB_UI:-true}"
OSMO_UI_API_HOSTNAME="${OSMO_UI_API_HOSTNAME:-osmo-service:80}"
OSMO_DATASET_BUCKET_NAME="${OSMO_DATASET_BUCKET_NAME:-aws-osmo}"
OSMO_DEPLOY_INTERNAL_ROUTER="${OSMO_DEPLOY_INTERNAL_ROUTER:-true}"
OSMO_INTERNAL_ROUTER_NAME="${OSMO_INTERNAL_ROUTER_NAME:-osmo-internal-router}"
OSMO_INTERNAL_ROUTER_IMAGE="${OSMO_INTERNAL_ROUTER_IMAGE:-$(version_value internal_router_image)}"
BACKEND_TOKEN_EXPIRES_AT="${BACKEND_TOKEN_EXPIRES_AT:-}"

if [[ "${OSMO_IMAGE_REGISTRY}" == nvcr.io* ]]; then
  load_ngc_api_key
fi

log "deploying OSMO ${OSMO_VERSION} with chart ${OSMO_CHART_VERSION}"

configure_kubectl

if [[ "${OSMO_INSTALL_KAI}" == "true" ]]; then
  "${ROOT_DIR}/infra/kubernetes/deploy-kai.sh"
fi

AWS_REGION="$(terraform_output aws_region)"
OSMO_NAMESPACE="$(terraform_output osmo_namespace)"
OSMO_WORKLOAD_NAMESPACE="$(terraform_output osmo_workload_namespace)"
OSMO_SERVICE_ACCOUNT_NAME="$(terraform_output osmo_service_account_name)"
OSMO_SERVICE_ACCOUNT_ROLE_ARN="$(terraform_output osmo_service_account_role_arn)"
OSMO_RUNTIME_SECRET_ARN="$(terraform_output osmo_runtime_secret_arn)"
if [[ -z "${OSMO_SERVICE_CALLBACK_URL:-}" ]]; then
  OSMO_SERVICE_CALLBACK_URL="${OSMO_WORKFLOW_CALLBACK_URL:-http://${OSMO_INTERNAL_ROUTER_NAME}.${OSMO_NAMESPACE}.svc.cluster.local}"
fi
if [[ -z "${OSMO_WORKFLOW_DATA_BASE_URL:-}" ]]; then
  OSMO_WORKFLOW_DATA_BASE_URL="http://${OSMO_INTERNAL_ROUTER_NAME}.${OSMO_NAMESPACE}.svc.cluster.local"
fi

SECRET_JSON="$(aws secretsmanager get-secret-value \
  --region "${AWS_REGION}" \
  --secret-id "${OSMO_RUNTIME_SECRET_ARN}" \
  --query SecretString \
  --output text)"

secret_field() {
  printf '%s' "${SECRET_JSON}" | jq -er --arg key "$1" '.[$key]'
}

POSTGRES_HOST="$(secret_field postgres_host)"
POSTGRES_PORT="$(secret_field postgres_port)"
POSTGRES_DATABASE="$(secret_field postgres_database)"
POSTGRES_USERNAME="$(secret_field postgres_username)"
POSTGRES_PASSWORD="$(secret_field postgres_password)"
REDIS_HOST="$(secret_field redis_host)"
REDIS_PORT="$(secret_field redis_port)"
REDIS_AUTH_TOKEN="$(secret_field redis_auth_token)"
DEFAULT_ADMIN_TOKEN="$(secret_field default_admin_token)"
OSMO_ARTIFACTS_BUCKET="$(secret_field osmo_artifacts_bucket)"
WORKFLOW_DATA_ACCESS_KEY_ID="$(secret_field workflow_data_access_key_id)"
WORKFLOW_DATA_SECRET_ACCESS_KEY="$(secret_field workflow_data_secret_access_key)"

kubectl create namespace "${OSMO_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace "${OSMO_WORKLOAD_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

if [[ "${OSMO_INSTALL_PODGROUP_COMPAT_CRD}" == "true" ]] &&
  ! kubectl get crd podgroups.scheduling.run.ai >/dev/null 2>&1; then
  log "installing minimal PodGroup compatibility CRD for OSMO CPU smoke workflows"
  kubectl apply -f - <<'YAML'
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: podgroups.scheduling.run.ai
  labels:
    app.kubernetes.io/name: osmo-podgroup-compat
    app.kubernetes.io/part-of: aws-osmo-reference
spec:
  group: scheduling.run.ai
  names:
    kind: PodGroup
    plural: podgroups
    singular: podgroup
  scope: Namespaced
  versions:
    - name: v2alpha2
      served: true
      storage: true
      schema:
        openAPIV3Schema:
          type: object
          x-kubernetes-preserve-unknown-fields: true
      subresources:
        status: {}
YAML
fi

kubectl -n "${OSMO_NAMESPACE}" create serviceaccount "${OSMO_SERVICE_ACCOUNT_NAME}" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "${OSMO_NAMESPACE}" annotate serviceaccount "${OSMO_SERVICE_ACCOUNT_NAME}" \
  "eks.amazonaws.com/role-arn=${OSMO_SERVICE_ACCOUNT_ROLE_ARN}" \
  --overwrite

IMAGE_PULL_SECRET=""
if [[ -n "${NGC_API_KEY:-}" ]]; then
  log "creating NGC image pull secret in OSMO namespaces"
  for namespace in "${OSMO_NAMESPACE}" "${OSMO_WORKLOAD_NAMESPACE}"; do
    kubectl -n "${namespace}" create secret docker-registry ngc-registry \
      --docker-server=nvcr.io \
      --docker-username="\$oauthtoken" \
      --docker-password="${NGC_API_KEY}" \
      --dry-run=client -o yaml | kubectl apply -f -
  done
  IMAGE_PULL_SECRET="ngc-registry"
fi

kubectl -n "${OSMO_NAMESPACE}" create secret generic db-secret \
  --from-literal=db-password="${POSTGRES_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n "${OSMO_NAMESPACE}" create secret generic redis-secret \
  --from-literal=redis-password="${REDIS_AUTH_TOKEN}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n "${OSMO_NAMESPACE}" create secret generic osmo-default-admin \
  --from-literal=password="${DEFAULT_ADMIN_TOKEN}" \
  --dry-run=client -o yaml | kubectl apply -f -

if MEK_YAML="$(kubectl -n "${OSMO_NAMESPACE}" get configmap mek-config -o jsonpath='{.data.mek\.yaml}' 2>/dev/null)"; then
  log "reusing existing OSMO MEK config"
else
  MEK_KEY="$(openssl rand -base64 32 | tr -d '\n')"
  MEK_JWK="$(printf '{"k":"%s","kid":"key1","kty":"oct"}' "${MEK_KEY}" | base64 | tr -d '\n')"
  MEK_YAML="$(printf 'currentMek: key1\nmeks:\n  key1: %s\n' "${MEK_JWK}")"
  kubectl -n "${OSMO_NAMESPACE}" create configmap mek-config \
    --from-literal=mek.yaml="${MEK_YAML}" \
    --dry-run=client -o yaml | kubectl apply -f -
fi

RUNTIME_CONFIG_CHECKSUM="$(
  printf '%s\n%s\n%s\n%s\n' "${POSTGRES_PASSWORD}" "${REDIS_AUTH_TOKEN}" "${DEFAULT_ADMIN_TOKEN}" "${MEK_YAML}" |
    openssl dgst -sha256 -r | awk '{print $1}'
)"

POD_MONITOR_ENABLED="false"
if [[ "${ENABLE_POD_MONITOR:-false}" == "true" ]]; then
  kubectl get crd podmonitors.monitoring.coreos.com >/dev/null
  POD_MONITOR_ENABLED="true"
fi

OSMO_CHART_SOURCE="repo"
OSMO_CHART_CACHE_DIR="${OSMO_CHART_CACHE_DIR:-$(helm env HELM_REPOSITORY_CACHE 2>/dev/null | tr -d '"')}"
if helm repo add osmo "${OSMO_CHART_REPO}" --force-update >/dev/null &&
  helm repo update osmo >/dev/null; then
  log "using OSMO Helm charts from ${OSMO_CHART_REPO}"
else
  log "OSMO Helm repo is unavailable; attempting local Helm cache fallback"
  OSMO_CHART_SOURCE="cache"
fi

osmo_chart_ref() {
  local chart="$1"
  if [[ "${OSMO_CHART_SOURCE}" == "repo" ]]; then
    printf 'osmo/%s' "${chart}"
    return
  fi

  local chart_path="${OSMO_CHART_CACHE_DIR}/${chart}-${OSMO_CHART_VERSION}.tgz"
  [[ -f "${chart_path}" ]] || die "OSMO chart ${chart} ${OSMO_CHART_VERSION} is not available in ${OSMO_CHART_CACHE_DIR}; retry when ${OSMO_CHART_REPO} is reachable"
  printf '%s' "${chart_path}"
}

osmo_config_update() {
  local log_file="$1"
  shift

  if osmo config update "$@" >"${log_file}" 2>&1; then
    return 0
  fi

  if grep -q "No changes were made to the config." "${log_file}"; then
    cat "${log_file}" >&2
    return 0
  fi

  return 1
}

aws_reference_pool_baseline() {
  jq -n '{
    description: "Default pool",
    download_type: "download",
    default_platform: "default",
    default_exec_timeout: "60d",
    default_queue_timeout: "60d",
    max_exec_timeout: "60d",
    max_queue_timeout: "60d",
    default_exit_actions: {},
    common_default_variables: {
      USER_CPU: 1,
      USER_GPU: 0,
      USER_MEMORY: "1Gi",
      USER_STORAGE: "1Gi"
    },
    common_resource_validations: [
      "default_cpu",
      "default_memory",
      "default_storage"
    ],
    common_pod_template: [
      "default_ctrl",
      "default_user"
    ],
    common_group_templates: [],
    enable_maintenance: false,
    resources: {
      gpu: null
    },
    topology_keys: [],
    platforms: {
      default: {
        description: "",
        host_network_allowed: false,
        privileged_allowed: false,
        allowed_mounts: [],
        default_mounts: [],
        default_variables: {},
        resource_validations: [],
        override_pod_template: []
      }
    }
  }'
}

SERVICE_VALUES="$(mktemp)"
BACKEND_VALUES="$(mktemp)"
UI_VALUES="$(mktemp)"
SERVICE_CONFIG="$(mktemp)"
WORKFLOW_CONFIG="$(mktemp)"
DATASET_CONFIG="$(mktemp)"
BACKEND_CONFIG="$(mktemp)"
POOL_CONFIG="$(mktemp)"
GPU_POD_TEMPLATE_CONFIG="$(mktemp)"
trap 'rm -f "${SERVICE_VALUES}" "${BACKEND_VALUES}" "${UI_VALUES}" "${SERVICE_CONFIG}" "${WORKFLOW_CONFIG}" "${DATASET_CONFIG}" "${BACKEND_CONFIG}" "${POOL_CONFIG}" "${GPU_POD_TEMPLATE_CONFIG}"; [[ -n "${PORT_FORWARD_PID:-}" ]] && kill "${PORT_FORWARD_PID}" >/dev/null 2>&1 || true' EXIT

cat >"${SERVICE_VALUES}" <<EOF
global:
  osmoImageLocation: "${OSMO_IMAGE_REGISTRY}"
  osmoImageTag: "${OSMO_IMAGE_TAG}"
  imagePullSecret: "${IMAGE_PULL_SECRET}"
  serviceAccountName: "${OSMO_SERVICE_ACCOUNT_NAME}"

serviceAccount:
  create: false
  name: "${OSMO_SERVICE_ACCOUNT_NAME}"

services:
  configFile:
    enabled: true
    path: /home/osmo/config/mek.yaml
  worker:
    scaling:
      minReplicas: 1
    extraPodAnnotations:
      aws.osmo.reference/runtime-config-checksum: "${RUNTIME_CONFIG_CHECKSUM}"
  logger:
    scaling:
      minReplicas: 1
    extraPodAnnotations:
      aws.osmo.reference/runtime-config-checksum: "${RUNTIME_CONFIG_CHECKSUM}"
  agent:
    scaling:
      minReplicas: 1
    extraPodAnnotations:
      aws.osmo.reference/runtime-config-checksum: "${RUNTIME_CONFIG_CHECKSUM}"
  delayedJobMonitor:
    extraPodAnnotations:
      aws.osmo.reference/runtime-config-checksum: "${RUNTIME_CONFIG_CHECKSUM}"
  postgres:
    enabled: false
    serviceName: "${POSTGRES_HOST}"
    port: ${POSTGRES_PORT}
    db: "${POSTGRES_DATABASE}"
    user: "${POSTGRES_USERNAME}"
    passwordSecretName: "db-secret"
    passwordSecretKey: "db-password"
  redis:
    enabled: false
    serviceName: "${REDIS_HOST}"
    port: ${REDIS_PORT}
    tlsEnabled: true
    passwordSecretName: "redis-secret"
    passwordSecretKey: "redis-password"
  defaultAdmin:
    enabled: true
    username: "admin"
    passwordSecretName: "osmo-default-admin"
    passwordSecretKey: "password"
  configs:
    enabled: false
  service:
    scaling:
      minReplicas: 1
    extraPodAnnotations:
      aws.osmo.reference/runtime-config-checksum: "${RUNTIME_CONFIG_CHECKSUM}"
    ingress:
      enabled: false

sidecars:
  envoy:
    enabled: false
  oauth2Proxy:
    enabled: false
  rateLimit:
    enabled: false
  authz:
    enabled: false

podMonitor:
  enabled: ${POD_MONITOR_ENABLED}
EOF

helm upgrade --install osmo-service "$(osmo_chart_ref service)" \
  --namespace "${OSMO_NAMESPACE}" \
  --version "${OSMO_CHART_VERSION}" \
  --values "${SERVICE_VALUES}" \
  --wait \
  --timeout 15m

kubectl -n "${OSMO_NAMESPACE}" rollout status deployment/osmo-service --timeout=10m

if [[ "${OSMO_DEPLOY_INTERNAL_ROUTER}" == "true" ]]; then
  kubectl -n "${OSMO_NAMESPACE}" apply -f - <<YAML
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${OSMO_INTERNAL_ROUTER_NAME}-nginx
  labels:
    app.kubernetes.io/name: ${OSMO_INTERNAL_ROUTER_NAME}
    app.kubernetes.io/part-of: aws-osmo-reference
data:
  default.conf: |
    server {
      listen 8080;
      client_max_body_size 0;
      proxy_read_timeout 3600s;
      proxy_send_timeout 3600s;

      proxy_http_version 1.1;
      proxy_set_header Host \$host;
      proxy_set_header X-Real-IP \$remote_addr;
      proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto \$scheme;
      proxy_set_header Upgrade \$http_upgrade;
      proxy_set_header Connection "upgrade";

      location /api/logger/ {
        proxy_pass http://osmo-logger.${OSMO_NAMESPACE}.svc.cluster.local;
      }

      location / {
        proxy_pass http://osmo-service.${OSMO_NAMESPACE}.svc.cluster.local;
      }
    }
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${OSMO_INTERNAL_ROUTER_NAME}
  labels:
    app.kubernetes.io/name: ${OSMO_INTERNAL_ROUTER_NAME}
    app.kubernetes.io/part-of: aws-osmo-reference
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: ${OSMO_INTERNAL_ROUTER_NAME}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: ${OSMO_INTERNAL_ROUTER_NAME}
        app.kubernetes.io/part-of: aws-osmo-reference
    spec:
      automountServiceAccountToken: false
      containers:
        - name: nginx
          image: ${OSMO_INTERNAL_ROUTER_IMAGE}
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 8080
          volumeMounts:
            - name: config
              mountPath: /etc/nginx/conf.d/default.conf
              subPath: default.conf
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              memory: 128Mi
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop:
                - ALL
            runAsNonRoot: true
            runAsUser: 101
            runAsGroup: 101
      volumes:
        - name: config
          configMap:
            name: ${OSMO_INTERNAL_ROUTER_NAME}-nginx
---
apiVersion: v1
kind: Service
metadata:
  name: ${OSMO_INTERNAL_ROUTER_NAME}
  labels:
    app.kubernetes.io/name: ${OSMO_INTERNAL_ROUTER_NAME}
    app.kubernetes.io/part-of: aws-osmo-reference
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: ${OSMO_INTERNAL_ROUTER_NAME}
  ports:
    - name: http
      port: 80
      targetPort: http
YAML
  kubectl -n "${OSMO_NAMESPACE}" rollout status "deployment/${OSMO_INTERNAL_ROUTER_NAME}" --timeout=5m
fi

kubectl -n "${OSMO_NAMESPACE}" port-forward svc/osmo-service 9000:80 >/tmp/osmo-service-port-forward.log 2>&1 &
PORT_FORWARD_PID="$!"

for _ in $(seq 1 60); do
  if port_open 127.0.0.1 9000; then
    break
  fi
  sleep 2
done

port_open 127.0.0.1 9000 || die "OSMO service port-forward did not become ready"
login_osmo_with_token "http://127.0.0.1:9000" "${DEFAULT_ADMIN_TOKEN}" || die "failed to log in to OSMO with default admin token"

# OSMO 6.2 uses SERVICE.service_base_url for workflow control-plane callbacks.
# The in-cluster logger service serves workflow log websocket endpoints, while
# workflow data and dataset API calls must reach the service API.
jq -n \
  --arg service_base_url "${OSMO_SERVICE_CALLBACK_URL}" \
  '{
    service_base_url: $service_base_url
  }' >"${SERVICE_CONFIG}"

if ! osmo_config_update /tmp/osmo-service-config.log SERVICE \
  --file "${SERVICE_CONFIG}" \
  --description "Configure in-cluster AWS OSMO service URL"; then
  cat /tmp/osmo-service-config.log >&2
  die "failed to configure OSMO service URL"
fi

jq -n \
  --arg data_endpoint "s3://${OSMO_ARTIFACTS_BUCKET}/workflow-data" \
  --arg log_endpoint "s3://${OSMO_ARTIFACTS_BUCKET}/workflow-logs" \
  --arg app_endpoint "s3://${OSMO_ARTIFACTS_BUCKET}/workflow-apps" \
  --arg access_key_id "${WORKFLOW_DATA_ACCESS_KEY_ID}" \
  --arg access_key "${WORKFLOW_DATA_SECRET_ACCESS_KEY}" \
  --arg region "${AWS_REGION}" \
  --arg workflow_data_base_url "${OSMO_WORKFLOW_DATA_BASE_URL}" \
  '{
    workflow_data: {
      base_url: $workflow_data_base_url,
      credential: {
        endpoint: $data_endpoint,
        access_key_id: $access_key_id,
        access_key: $access_key,
        region: $region
      }
    },
    workflow_log: {
      credential: {
        endpoint: $log_endpoint,
        access_key_id: $access_key_id,
        access_key: $access_key,
        region: $region
      }
    },
    workflow_app: {
      credential: {
        endpoint: $app_endpoint,
        access_key_id: $access_key_id,
        access_key: $access_key,
        region: $region
      }
    },
    credential_config: {
      disable_data_validation: ["s3"]
    }
  }' >"${WORKFLOW_CONFIG}"

if ! osmo_config_update /tmp/osmo-workflow-config.log WORKFLOW \
  --file "${WORKFLOW_CONFIG}" \
  --description "Configure AWS workflow storage"; then
  cat /tmp/osmo-workflow-config.log >&2
  die "failed to configure OSMO workflow storage"
fi

jq -n \
  --arg bucket_name "${OSMO_DATASET_BUCKET_NAME}" \
  --arg dataset_path "s3://${OSMO_ARTIFACTS_BUCKET}/datasets" \
  --arg region "${AWS_REGION}" \
  '{
    buckets: {
      ($bucket_name): {
        dataset_path: $dataset_path,
        region: $region,
        mode: "read-write"
      }
    },
    default_bucket: $bucket_name
  }' >"${DATASET_CONFIG}"

if ! osmo_config_update /tmp/osmo-dataset-config.log DATASET \
  --file "${DATASET_CONFIG}" \
  --description "Configure AWS dataset bucket"; then
  cat /tmp/osmo-dataset-config.log >&2
  die "failed to configure OSMO dataset bucket"
fi

osmo credential delete aws-osmo-dataset >/dev/null 2>&1 || true
if ! osmo credential set aws-osmo-dataset \
  --type DATA \
  --payload \
  endpoint="s3://${OSMO_ARTIFACTS_BUCKET}" \
  region="${AWS_REGION}" \
  access_key_id="${WORKFLOW_DATA_ACCESS_KEY_ID}" \
  access_key="${WORKFLOW_DATA_SECRET_ACCESS_KEY}" >/tmp/osmo-dataset-credential.log 2>&1; then
  cat /tmp/osmo-dataset-credential.log >&2
  die "failed to configure OSMO dataset credential"
fi

osmo profile set bucket "${OSMO_DATASET_BUCKET_NAME}" >/dev/null

if ! osmo user create backend-operator --roles osmo-backend >/tmp/osmo-backend-user.log 2>&1; then
  osmo user get backend-operator >/dev/null 2>&1 || {
    cat /tmp/osmo-backend-user.log >&2
    die "failed to create or verify backend-operator user"
  }
fi

osmo token delete backend-token --user backend-operator >/tmp/osmo-backend-token-delete.log 2>&1 || true

TOKEN_ARGS=(
  token set backend-token
  --user backend-operator
  --description "AWS reference backend operator token"
  --roles osmo-backend
  -t json
)
if [[ -n "${BACKEND_TOKEN_EXPIRES_AT}" ]]; then
  TOKEN_ARGS+=(--expires-at "${BACKEND_TOKEN_EXPIRES_AT}")
fi

if ! TOKEN_OUTPUT="$(osmo "${TOKEN_ARGS[@]}" 2>/tmp/osmo-backend-token.log)"; then
  cat /tmp/osmo-backend-token.log >&2
  die "failed to generate backend operator token"
fi

BACKEND_TOKEN="$(printf '%s' "${TOKEN_OUTPUT}" | jq -er '.token // .access_token // .accessToken // .value' 2>/dev/null || true)"
if [[ -z "${BACKEND_TOKEN}" ]]; then
  BACKEND_TOKEN="$(printf '%s' "${TOKEN_OUTPUT}" | tail -n 1 | tr -d '\r')"
fi
[[ -n "${BACKEND_TOKEN}" ]] || die "backend operator token generation returned an empty token"

kubectl -n "${OSMO_NAMESPACE}" create secret generic backend-operator-token \
  --from-literal=token="${BACKEND_TOKEN}" \
  --dry-run=client -o yaml | kubectl apply -f -

cat >"${BACKEND_VALUES}" <<EOF
global:
  osmoImageLocation: "${OSMO_IMAGE_REGISTRY}"
  osmoImageTag: "${OSMO_IMAGE_TAG}"
  imagePullSecret: "${IMAGE_PULL_SECRET}"
  serviceUrl: "http://osmo-agent.${OSMO_NAMESPACE}.svc.cluster.local"
  backendName: "${OSMO_BACKEND_NAME}"
  backendNamespace: "${OSMO_WORKLOAD_NAMESPACE}"
  agentNamespace: "${OSMO_NAMESPACE}"
  accountTokenSecret: "backend-operator-token"
  accountTokenSecretKey: "token"
  loginMethod: "token"
  includeNamespaceUsage: "${OSMO_WORKLOAD_NAMESPACE}"

services:
  backendListener:
    resources:
      requests:
        cpu: "100m"
        memory: "256Mi"
      limits:
        memory: "512Mi"
  backendWorker:
    resources:
      requests:
        cpu: "100m"
        memory: "256Mi"
      limits:
        memory: "512Mi"

backendTestRunner:
  enabled: false

podMonitor:
  enabled: ${POD_MONITOR_ENABLED}
EOF

helm upgrade --install osmo-backend "$(osmo_chart_ref backend-operator)" \
  --namespace "${OSMO_NAMESPACE}" \
  --version "${OSMO_CHART_VERSION}" \
  --values "${BACKEND_VALUES}" \
  --wait \
  --timeout 15m

kubectl -n "${OSMO_NAMESPACE}" rollout status deployment/osmo-backend-osmo-backend-listener --timeout=10m
kubectl -n "${OSMO_NAMESPACE}" rollout status deployment/osmo-backend-osmo-backend-worker --timeout=10m

for _ in $(seq 1 60); do
  BACKEND_CURRENT="$(osmo config show BACKEND "${OSMO_BACKEND_NAME}" 2>/dev/null || true)"
  if [[ -n "${BACKEND_CURRENT}" ]]; then
    break
  fi
  sleep 5
done
[[ -n "${BACKEND_CURRENT:-}" ]] || die "OSMO backend ${OSMO_BACKEND_NAME} did not register with the service"

jq -n \
  --arg scheduler_name "${OSMO_K8S_SCHEDULER_NAME}" \
  '{
    description: "Default AWS reference backend",
    scheduler_settings: {
      scheduler_type: "kai",
      scheduler_name: $scheduler_name,
      scheduler_timeout: 30
    }
  }' >"${BACKEND_CONFIG}"

if printf '%s' "${BACKEND_CURRENT}" | jq -e \
  --arg scheduler_name "${OSMO_K8S_SCHEDULER_NAME}" \
  '.scheduler_settings.scheduler_type == "kai" and
   .scheduler_settings.scheduler_name == $scheduler_name and
   .scheduler_settings.scheduler_timeout == 30' >/dev/null; then
  log "OSMO backend scheduler is already configured"
else
  if ! osmo_config_update /tmp/osmo-backend-config.log BACKEND "${OSMO_BACKEND_NAME}" \
    --file "${BACKEND_CONFIG}" \
    --description "Configure AWS backend scheduler"; then
    cat /tmp/osmo-backend-config.log >&2
    die "failed to configure OSMO backend scheduler"
  fi
fi

if [[ "${OSMO_CONFIGURE_GPU_PLATFORM}" == "true" ]]; then
  POD_TEMPLATE_CURRENT="$(osmo config show POD_TEMPLATE 2>/dev/null || printf '{}')"
  printf '%s' "${POD_TEMPLATE_CURRENT}" | jq \
    --arg name "${OSMO_GPU_POD_TEMPLATE_NAME}" \
    --arg label_key "${OSMO_GPU_PLATFORM_LABEL_KEY}" \
    --arg label_value "${OSMO_GPU_PLATFORM_LABEL_VALUE}" \
    --arg do_not_disrupt "${OSMO_GPU_POD_DO_NOT_DISRUPT}" \
    --arg shm_size "${OSMO_GPU_POD_SHM_SIZE}" \
    '.[$name] = {
       metadata: {
         annotations: {
           "karpenter.sh/do-not-disrupt": $do_not_disrupt
         }
       },
       spec: {
         nodeSelector: {
           ($label_key): $label_value
         },
         tolerations: [
           {
             key: "nvidia.com/gpu",
             operator: "Exists",
             effect: "NoSchedule"
           }
         ],
         containers: [
           {
             name: "{{USER_CONTAINER_NAME}}",
             volumeMounts: [
               {
                 name: "shm",
                 mountPath: "/dev/shm"
               }
             ]
           }
         ],
         volumes: [
           {
             name: "shm",
             emptyDir: {
               medium: "Memory",
               sizeLimit: $shm_size
             }
           }
         ]
       }
     }' >"${GPU_POD_TEMPLATE_CONFIG}"

  if ! osmo_config_update /tmp/osmo-gpu-pod-template.log POD_TEMPLATE \
    --file "${GPU_POD_TEMPLATE_CONFIG}" \
    --description "Configure AWS G7e GPU pod template"; then
    cat /tmp/osmo-gpu-pod-template.log >&2
    die "failed to configure OSMO G7e pod template"
  fi

  if ! POOL_CURRENT="$(osmo config show POOL default 2>/tmp/osmo-pool-show.log)"; then
    cat /tmp/osmo-pool-show.log >&2
    log "OSMO pool config is unreadable; applying AWS reference pool baseline"
    POOL_CURRENT="$(aws_reference_pool_baseline)"
  fi

  if printf '%s' "${POOL_CURRENT}" | jq -e \
    --arg platform "${OSMO_GPU_PLATFORM_NAME}" \
    --arg pod_template "${OSMO_GPU_POD_TEMPLATE_NAME}" \
    --arg label_key "${OSMO_GPU_PLATFORM_LABEL_KEY}" \
    --arg label_value "${OSMO_GPU_PLATFORM_LABEL_VALUE}" \
    --arg do_not_disrupt "${OSMO_GPU_POD_DO_NOT_DISRUPT}" \
    --arg shm_size "${OSMO_GPU_POD_SHM_SIZE}" \
    '.download_type == "download" and
     ((.common_pod_template // []) | all(startswith("fsx_lustre") | not)) and
     ((.platforms.default.default_mounts // []) | all(. != "/mnt/osmo-fsx")) and
     .platforms[$platform].labels[$label_key] == $label_value and
     .parsed_pod_template[$pod_template].metadata.annotations["karpenter.sh/do-not-disrupt"] == $do_not_disrupt and
     any(.parsed_pod_template[$pod_template].spec.containers[]?; .name == "{{USER_CONTAINER_NAME}}" and any(.volumeMounts[]?; .name == "shm" and .mountPath == "/dev/shm")) and
     any(.parsed_pod_template[$pod_template].spec.volumes[]?; .name == "shm" and .emptyDir.medium == "Memory" and .emptyDir.sizeLimit == $shm_size) and
     any(.platforms[$platform].tolerations[]?; .key == "nvidia.com/gpu" and .operator == "Exists" and .effect == "NoSchedule") and
     any(.platforms[$platform].override_pod_template[]?; . == $pod_template)' >/dev/null; then
    log "OSMO pool GPU platform is already configured"
  else
    printf '%s' "${POOL_CURRENT}" | jq \
      --arg platform "${OSMO_GPU_PLATFORM_NAME}" \
      --arg pod_template "${OSMO_GPU_POD_TEMPLATE_NAME}" \
      'del(.last_heartbeat, .parsed_resource_validations, .parsed_pod_template, .parsed_group_templates) |
       .download_type = "download" |
       .common_pod_template = ((.common_pod_template // ["default_ctrl", "default_user"]) | map(select(startswith("fsx_lustre") | not))) |
       .platforms.default.default_mounts = ((.platforms.default.default_mounts // []) | map(select(. != "/mnt/osmo-fsx"))) |
       .platforms[$platform] = {
         description: "AWS G7e RTX PRO 6000 Blackwell platform",
         host_network_allowed: false,
         privileged_allowed: false,
         allowed_mounts: [],
         default_mounts: [],
         default_variables: {},
         resource_validations: [],
         override_pod_template: [$pod_template]
       }' >"${POOL_CONFIG}"

    if ! osmo_config_update /tmp/osmo-pool-config.log POOL default \
      --file "${POOL_CONFIG}" \
      --description "Configure AWS G7e GPU platform"; then
      cat /tmp/osmo-pool-config.log >&2
      die "failed to configure OSMO pool GPU platform"
    fi
  fi
fi

if [[ "${OSMO_CONFIGURE_GPU_PLATFORM}" == "true" ]]; then
  POD_TEMPLATE_CURRENT="$(osmo config show POD_TEMPLATE 2>/dev/null || printf '{}')"
  printf '%s' "${POD_TEMPLATE_CURRENT}" | jq \
    --arg name "${OSMO_G6E_GPU_POD_TEMPLATE_NAME}" \
    --arg label_key "${OSMO_GPU_PLATFORM_LABEL_KEY}" \
    --arg label_value "${OSMO_G6E_GPU_PLATFORM_LABEL_VALUE}" \
    --arg do_not_disrupt "${OSMO_GPU_POD_DO_NOT_DISRUPT}" \
    --arg shm_size "${OSMO_GPU_POD_SHM_SIZE}" \
    '.[$name] = {
       metadata: {
         annotations: {
           "karpenter.sh/do-not-disrupt": $do_not_disrupt
         }
       },
       spec: {
         nodeSelector: {
           ($label_key): $label_value
         },
         tolerations: [
           {
             key: "nvidia.com/gpu",
             operator: "Exists",
             effect: "NoSchedule"
           }
         ],
         containers: [
           {
             name: "{{USER_CONTAINER_NAME}}",
             volumeMounts: [
               {
                 name: "shm",
                 mountPath: "/dev/shm"
               }
             ]
           }
         ],
         volumes: [
           {
             name: "shm",
             emptyDir: {
               medium: "Memory",
               sizeLimit: $shm_size
             }
           }
         ]
       }
     }' >"${GPU_POD_TEMPLATE_CONFIG}"

  if ! osmo_config_update /tmp/osmo-g6e-gpu-pod-template.log POD_TEMPLATE \
    --file "${GPU_POD_TEMPLATE_CONFIG}" \
    --description "Configure AWS G6e GPU pod template"; then
    cat /tmp/osmo-g6e-gpu-pod-template.log >&2
    die "failed to configure OSMO G6e pod template"
  fi

  if ! POOL_CURRENT="$(osmo config show POOL default 2>/tmp/osmo-pool-show.log)"; then
    cat /tmp/osmo-pool-show.log >&2
    log "OSMO pool config is unreadable; applying AWS reference pool baseline"
    POOL_CURRENT="$(aws_reference_pool_baseline)"
  fi

  printf '%s' "${POOL_CURRENT}" | jq \
    --arg platform "${OSMO_G6E_GPU_PLATFORM_NAME}" \
    --arg pod_template "${OSMO_G6E_GPU_POD_TEMPLATE_NAME}" \
    'del(.last_heartbeat, .parsed_resource_validations, .parsed_pod_template, .parsed_group_templates) |
     .download_type = "download" |
     .common_pod_template = ((.common_pod_template // ["default_ctrl", "default_user"]) | map(select(startswith("fsx_lustre") | not))) |
     .platforms.default.default_mounts = ((.platforms.default.default_mounts // []) | map(select(. != "/mnt/osmo-fsx"))) |
     .platforms[$platform] = {
       description: "AWS G6e L40S platform",
       host_network_allowed: false,
       privileged_allowed: false,
       allowed_mounts: [],
       default_mounts: [],
       default_variables: {},
       resource_validations: [],
       override_pod_template: [$pod_template]
     }' >"${POOL_CONFIG}"

  if ! osmo_config_update /tmp/osmo-g6e-pool-config.log POOL default \
    --file "${POOL_CONFIG}" \
    --description "Configure AWS G6e GPU platform"; then
    cat /tmp/osmo-g6e-pool-config.log >&2
    die "failed to configure OSMO pool G6e platform"
  fi
fi

if [[ "${OSMO_CONFIGURE_EFA_PLATFORMS}" == "true" ]]; then
  POD_TEMPLATE_CURRENT="$(osmo config show POD_TEMPLATE 2>/dev/null || printf '{}')"
  printf '%s' "${POD_TEMPLATE_CURRENT}" | jq \
    --arg g7e_name "${OSMO_G7E_EFA_POD_TEMPLATE_NAME}" \
    --arg g7e_label_value "${OSMO_G7E_EFA_PLATFORM_LABEL_VALUE}" \
    --arg g6e_name "${OSMO_G6E_EFA_POD_TEMPLATE_NAME}" \
    --arg g6e_label_value "${OSMO_G6E_EFA_PLATFORM_LABEL_VALUE}" \
    --arg label_key "${OSMO_GPU_PLATFORM_LABEL_KEY}" \
    --arg do_not_disrupt "${OSMO_GPU_POD_DO_NOT_DISRUPT}" \
    --arg shm_size "${OSMO_GPU_POD_SHM_SIZE}" \
    --arg efa_resource_name "${OSMO_EFA_RESOURCE_NAME}" \
    --arg efa_resource_count "${OSMO_EFA_RESOURCE_COUNT}" \
    'def efa_template($label_value): {
       metadata: {
         annotations: {
           "karpenter.sh/do-not-disrupt": $do_not_disrupt
         }
       },
       spec: {
         nodeSelector: {
           ($label_key): $label_value
         },
         tolerations: [
           {
             key: "nvidia.com/gpu",
             operator: "Exists",
             effect: "NoSchedule"
           },
           {
             key: $efa_resource_name,
             operator: "Exists",
             effect: "NoSchedule"
           }
         ],
         affinity: {
           podAntiAffinity: {
             requiredDuringSchedulingIgnoredDuringExecution: [
               {
                 labelSelector: {
                   matchLabels: {
                     "osmo.workflow_id": "{{WF_ID}}"
                   }
                 },
                 topologyKey: "kubernetes.io/hostname"
               }
             ]
           }
         },
         containers: [
           {
             name: "{{USER_CONTAINER_NAME}}",
             resources: {
               requests: {
                 ($efa_resource_name): $efa_resource_count
               },
               limits: {
                 ($efa_resource_name): $efa_resource_count
               }
             },
             volumeMounts: [
               {
                 name: "shm",
                 mountPath: "/dev/shm"
               }
             ]
           }
         ],
         volumes: [
           {
             name: "shm",
             emptyDir: {
               medium: "Memory",
               sizeLimit: $shm_size
             }
           }
         ]
       }
     };
     .[$g7e_name] = efa_template($g7e_label_value) |
     .[$g6e_name] = efa_template($g6e_label_value)' >"${GPU_POD_TEMPLATE_CONFIG}"

  if ! osmo_config_update /tmp/osmo-efa-pod-template.log POD_TEMPLATE \
    --file "${GPU_POD_TEMPLATE_CONFIG}" \
    --description "Configure AWS EFA pod templates"; then
    cat /tmp/osmo-efa-pod-template.log >&2
    die "failed to configure OSMO EFA pod templates"
  fi

  if ! POOL_CURRENT="$(osmo config show POOL default 2>/tmp/osmo-pool-show.log)"; then
    cat /tmp/osmo-pool-show.log >&2
    log "OSMO pool config is unreadable; applying AWS reference pool baseline"
    POOL_CURRENT="$(aws_reference_pool_baseline)"
  fi

  printf '%s' "${POOL_CURRENT}" | jq \
    --arg g7e_platform "${OSMO_G7E_EFA_PLATFORM_NAME}" \
    --arg g7e_pod_template "${OSMO_G7E_EFA_POD_TEMPLATE_NAME}" \
    --arg g6e_platform "${OSMO_G6E_EFA_PLATFORM_NAME}" \
    --arg g6e_pod_template "${OSMO_G6E_EFA_POD_TEMPLATE_NAME}" \
    'def efa_platform($description; $pod_template): {
       description: $description,
       host_network_allowed: false,
       privileged_allowed: false,
       allowed_mounts: [],
       default_mounts: [],
       default_variables: {},
       resource_validations: [],
       override_pod_template: [$pod_template]
     };
     del(.last_heartbeat, .parsed_resource_validations, .parsed_pod_template, .parsed_group_templates) |
     .download_type = "download" |
     .common_pod_template = ((.common_pod_template // ["default_ctrl", "default_user"]) | map(select(startswith("fsx_lustre") | not))) |
     .platforms.default.default_mounts = ((.platforms.default.default_mounts // []) | map(select(. != "/mnt/osmo-fsx"))) |
     .platforms[$g7e_platform] = efa_platform("AWS G7e RTX PRO 6000 Blackwell EFA platform"; $g7e_pod_template) |
     .platforms[$g6e_platform] = efa_platform("AWS G6e L40S EFA platform"; $g6e_pod_template)' >"${POOL_CONFIG}"

  if ! osmo_config_update /tmp/osmo-efa-pool-config.log POOL default \
    --file "${POOL_CONFIG}" \
    --description "Configure AWS EFA platforms"; then
    cat /tmp/osmo-efa-pool-config.log >&2
    die "failed to configure OSMO pool EFA platforms"
  fi
fi

if [[ "${OSMO_DEPLOY_WEB_UI}" == "true" ]]; then
  cat >"${UI_VALUES}" <<EOF
global:
  osmoImageLocation: "${OSMO_IMAGE_REGISTRY}"
  osmoImageTag: "${OSMO_IMAGE_TAG}"
  imagePullSecret: "${IMAGE_PULL_SECRET}"

services:
  ui:
    apiHostname: "${OSMO_UI_API_HOSTNAME}"
    ingress:
      enabled: false

sidecars:
  envoy:
    enabled: false
  oauth2Proxy:
    enabled: false
EOF

  helm upgrade --install osmo-ui "$(osmo_chart_ref web-ui)" \
    --namespace "${OSMO_NAMESPACE}" \
    --version "${OSMO_CHART_VERSION}" \
    --values "${UI_VALUES}" \
    --wait \
    --timeout 10m

  kubectl -n "${OSMO_NAMESPACE}" rollout status deployment/osmo-ui --timeout=10m
fi

log "OSMO service and backend operator deployment completed"
