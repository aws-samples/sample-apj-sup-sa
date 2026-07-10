#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=../../scripts/common.sh
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/common.sh"

require_cmds aws curl helm jq kubectl terraform

CORE_TF_DIR="${CORE_TF_DIR:-${ROOT_DIR}/infra/core}"
OBSERVABILITY_TF_DIR="${OBSERVABILITY_TF_DIR:-${ROOT_DIR}/infra/observability}"
OSMO_NAMESPACE="${OSMO_NAMESPACE:-${TF_OUTPUT_OSMO_NAMESPACE:-osmo}}"
OSMO_BACKEND_NAME="${OSMO_BACKEND_NAME:-default}"
OSMO_SERVICE_LOCAL_PORT="${OSMO_SERVICE_LOCAL_PORT:-9000}"
OSMO_DASHBOARD_DIR="${OSMO_DASHBOARD_DIR:-${OBSERVABILITY_TF_DIR}/dashboards}"
GPU_OPERATOR_NAMESPACE="${GPU_OPERATOR_NAMESPACE:-$(version_value gpu_operator_namespace)}"

core_output() {
  TF_DIR="${CORE_TF_DIR}" terraform_output "$1"
}

observability_output() {
  terraform -chdir="${OBSERVABILITY_TF_DIR}" output -raw "$1"
}

set_tf_var_default() {
  local key="$1"
  local value="$2"
  local env_key
  env_key="TF_VAR_${key}"
  if [[ -z "${!env_key:-}" ]]; then
    export "${env_key}=${value}"
  fi
}

grafana_api() {
  local method="$1"
  local path="$2"
  local data_file="${3:-}"
  local args=(-fsS -X "${method}" -H "Authorization: Bearer ${GRAFANA_TOKEN}" -H "Content-Type: application/json")

  if [[ -n "${data_file}" ]]; then
    args+=(--data-binary "@${data_file}")
  fi

  curl "${args[@]}" "${AMG_WORKSPACE_URL}${path}"
}

grafana_status() {
  local path="$1"
  local output_file="$2"

  curl -sS -o "${output_file}" -w "%{http_code}" \
    -H "Authorization: Bearer ${GRAFANA_TOKEN}" \
    "${AMG_WORKSPACE_URL}${path}"
}

upsert_grafana_datasource() {
  local payload status response
  payload="$(mktemp)"
  response="$(mktemp)"

  jq -n \
    --arg name "${GRAFANA_DATASOURCE_NAME}" \
    --arg uid "${GRAFANA_DATASOURCE_UID}" \
    --arg url "${AMP_PROMETHEUS_ENDPOINT}" \
    --arg region "${AWS_REGION}" \
    '{
      access: "proxy",
      isDefault: true,
      name: $name,
      type: "prometheus",
      uid: $uid,
      url: $url,
      jsonData: {
        httpMethod: "POST",
        sigV4Auth: true,
        sigV4AuthType: "default",
        sigV4Region: $region,
        timeInterval: "15s"
      }
    }' >"${payload}"

  status="$(grafana_status "/api/datasources/uid/${GRAFANA_DATASOURCE_UID}" "${response}")"
  if [[ "${status}" == "200" ]]; then
    grafana_api PUT "/api/datasources/uid/${GRAFANA_DATASOURCE_UID}" "${payload}" >/dev/null
  else
    grafana_api POST "/api/datasources" "${payload}" >/dev/null
  fi

  rm -f "${payload}" "${response}"
}

import_dashboard() {
  local dashboard_file="$1"
  local transformed payload
  transformed="$(mktemp)"
  payload="$(mktemp)"

  jq --arg datasource_uid "${GRAFANA_DATASOURCE_UID}" '
    def walk(f):
      . as $in
      | if type == "object" then
          reduce keys[] as $key ({}; . + {($key): ($in[$key] | walk(f))}) | f
        elif type == "array" then map(walk(f)) | f
        else f
        end;

    del(.id)
    | walk(
        if type == "object" and (.datasource? | type == "object") and .datasource.type == "prometheus" then
          .datasource.uid = $datasource_uid
        else
          .
        end
      )
    | if .templating.list then
        .templating.list |= map(
          if .type == "datasource" and .query == "prometheus" then
            .current = {selected: true, text: "AMP", value: $datasource_uid}
          else
            .
          end
        )
      else
        .
      end
  ' "${dashboard_file}" >"${transformed}"

  jq -n --slurpfile dashboard "${transformed}" '{dashboard: $dashboard[0], overwrite: true}' >"${payload}"
  grafana_api POST "/api/dashboards/db" "${payload}" >/dev/null

  rm -f "${transformed}" "${payload}"
}

import_aws_osmo_overview_dashboard() {
  local payload
  payload="$(mktemp)"

  jq -n \
    --arg datasource_uid "${GRAFANA_DATASOURCE_UID}" \
    --arg amp_workspace_id "${AMP_WORKSPACE_ID}" \
    --arg amg_workspace_id "${AMG_WORKSPACE_ID}" \
    --arg datasource_name "${GRAFANA_DATASOURCE_NAME}" \
    '{
      dashboard: {
        id: null,
        uid: "aws-osmo-overview",
        title: "AWS OSMO Overview",
        schemaVersion: 39,
        version: 0,
        refresh: "30s",
        time: {from: "now-1h", to: "now"},
        panels: [
          {
            id: 1,
            type: "stat",
            title: "Healthy OSMO scrape targets",
            datasource: {type: "prometheus", uid: $datasource_uid},
            gridPos: {h: 8, w: 8, x: 0, y: 0},
            targets: [{refId: "A", expr: "sum(up{namespace=\"osmo\"})"}],
            fieldConfig: {
              defaults: {
                color: {mode: "thresholds"},
                thresholds: {mode: "absolute", steps: [{color: "red", value: null}, {color: "green", value: 1}]}
              },
              overrides: []
            },
            options: {colorMode: "value", graphMode: "area", justifyMode: "center", orientation: "auto", reduceOptions: {calcs: ["lastNotNull"], fields: "", values: false}, textMode: "auto"}
          },
          {
            id: 2,
            type: "timeseries",
            title: "OSMO target health",
            datasource: {type: "prometheus", uid: $datasource_uid},
            gridPos: {h: 8, w: 16, x: 8, y: 0},
            targets: [{refId: "A", expr: "up{namespace=\"osmo\"}", legendFormat: "{{pod}}"}]
          },
          {
            id: 3,
            type: "stat",
            title: "Workflow pods observed in AMP over 24h",
            datasource: {type: "prometheus", uid: $datasource_uid},
            gridPos: {h: 8, w: 8, x: 0, y: 8},
            targets: [{refId: "A", expr: "count(count_over_time(kube_pod_info{namespace=\"osmo-workflows\"}[24h]))"}],
            fieldConfig: {
              defaults: {
                color: {mode: "thresholds"},
                thresholds: {mode: "absolute", steps: [{color: "gray", value: null}, {color: "green", value: 1}]}
              },
              overrides: []
            },
            options: {colorMode: "value", graphMode: "area", justifyMode: "center", orientation: "auto", reduceOptions: {calcs: ["lastNotNull"], fields: "", values: false}, textMode: "auto"}
          },
          {
            id: 4,
            type: "table",
            title: "Workflow pods observed over 24h",
            datasource: {type: "prometheus", uid: $datasource_uid},
            gridPos: {h: 8, w: 16, x: 8, y: 8},
            targets: [{refId: "A", expr: "count_over_time(kube_pod_info{namespace=\"osmo-workflows\"}[24h])", format: "table", instant: true}]
          },
          {
            id: 5,
            type: "timeseries",
            title: "GPU utilization",
            datasource: {type: "prometheus", uid: $datasource_uid},
            gridPos: {h: 8, w: 12, x: 0, y: 16},
            targets: [{refId: "A", expr: "DCGM_FI_DEV_GPU_UTIL{exported_namespace=\"osmo-workflows\"}", legendFormat: "{{exported_pod}} gpu={{gpu}}"}]
          },
          {
            id: 6,
            type: "timeseries",
            title: "GPU framebuffer used",
            datasource: {type: "prometheus", uid: $datasource_uid},
            gridPos: {h: 8, w: 12, x: 12, y: 16},
            targets: [{refId: "A", expr: "DCGM_FI_DEV_FB_USED{exported_namespace=\"osmo-workflows\"}", legendFormat: "{{exported_pod}} gpu={{gpu}}"}]
          },
          {
            id: 7,
            type: "timeseries",
            title: "GPU power usage",
            datasource: {type: "prometheus", uid: $datasource_uid},
            gridPos: {h: 8, w: 12, x: 0, y: 24},
            targets: [{refId: "A", expr: "DCGM_FI_DEV_POWER_USAGE{exported_namespace=\"osmo-workflows\"}", legendFormat: "{{exported_pod}} gpu={{gpu}}"}]
          },
          {
            id: 8,
            type: "timeseries",
            title: "GPU temperature",
            datasource: {type: "prometheus", uid: $datasource_uid},
            gridPos: {h: 8, w: 12, x: 12, y: 24},
            targets: [{refId: "A", expr: "DCGM_FI_DEV_GPU_TEMP{exported_namespace=\"osmo-workflows\"}", legendFormat: "{{exported_pod}} gpu={{gpu}}"}]
          },
          {
            id: 9,
            type: "table",
            title: "Current OSMO scrape targets",
            datasource: {type: "prometheus", uid: $datasource_uid},
            gridPos: {h: 8, w: 24, x: 0, y: 32},
            targets: [{refId: "A", expr: "up{namespace=\"osmo\"}", format: "table", instant: true}]
          },
          {
            id: 10,
            type: "text",
            title: "AWS managed endpoints",
            gridPos: {h: 6, w: 24, x: 0, y: 40},
            options: {
              mode: "markdown",
              content: ("AMP workspace: `" + $amp_workspace_id + "`\\n\\nAMG workspace: `" + $amg_workspace_id + "`\\n\\nData source: `" + $datasource_name + "`")
            }
          }
        ]
      },
      overwrite: true
    }' >"${payload}"

  grafana_api POST "/api/dashboards/db" "${payload}" >/dev/null
  rm -f "${payload}"
}

enable_dcgm_servicemonitor() {
  if ! kubectl get namespace "${GPU_OPERATOR_NAMESPACE}" >/dev/null 2>&1; then
    log "skipping DCGM ServiceMonitor because namespace ${GPU_OPERATOR_NAMESPACE} does not exist"
    return 0
  fi

  kubectl apply -f - <<YAML
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: nvidia-dcgm-exporter
  namespace: ${MONITORING_NAMESPACE}
  labels:
    app: nvidia-dcgm-exporter
    aws.osmo.reference/component: observability
spec:
  namespaceSelector:
    matchNames:
    - ${GPU_OPERATOR_NAMESPACE}
  selector:
    matchLabels:
      app: nvidia-dcgm-exporter
  endpoints:
  - port: gpu-metrics
    path: /metrics
    interval: 15s
YAML
}

enable_osmo_podmonitors() {
  local chart_repo chart_version
  chart_repo="${OSMO_CHART_REPO:-$(version_value chart_repository)}"
  chart_version="${OSMO_CHART_VERSION:-$(version_value chart_version)}"

  helm repo add osmo "${chart_repo}" --force-update >/dev/null
  helm repo update osmo >/dev/null

  helm upgrade osmo-service osmo/service \
    --namespace "${OSMO_NAMESPACE}" \
    --version "${chart_version}" \
    --reuse-values \
    --set podMonitor.enabled=true \
    --wait \
    --timeout 15m

  helm upgrade osmo-backend osmo/backend-operator \
    --namespace "${OSMO_NAMESPACE}" \
    --version "${chart_version}" \
    --reuse-values \
    --set podMonitor.enabled=true \
    --wait \
    --timeout 15m
}

update_osmo_backend_grafana_url() {
  local default_admin_token service_url use_local_port_forward port_forward_pid backend_current backend_config update_log
  service_url="${OSMO_SERVICE_URL:-http://127.0.0.1:${OSMO_SERVICE_LOCAL_PORT}}"
  use_local_port_forward="false"
  port_forward_pid=""
  backend_config="$(mktemp)"
  update_log="$(mktemp)"

  if [[ "${service_url}" == "http://127.0.0.1:${OSMO_SERVICE_LOCAL_PORT}" ]]; then
    use_local_port_forward="true"
  fi

  if [[ "${use_local_port_forward}" == "true" ]] && ! port_open 127.0.0.1 "${OSMO_SERVICE_LOCAL_PORT}"; then
    kubectl -n "${OSMO_NAMESPACE}" port-forward "svc/osmo-service" "${OSMO_SERVICE_LOCAL_PORT}:80" >/tmp/osmo-observability-service-port-forward.log 2>&1 &
    port_forward_pid="$!"
    for _ in $(seq 1 60); do
      port_open 127.0.0.1 "${OSMO_SERVICE_LOCAL_PORT}" && break
      sleep 2
    done
  fi

  if [[ "${use_local_port_forward}" == "true" ]]; then
    port_open 127.0.0.1 "${OSMO_SERVICE_LOCAL_PORT}" || die "OSMO service port-forward did not become ready"
  fi

  default_admin_token="$(kubectl -n "${OSMO_NAMESPACE}" get secret osmo-default-admin -o jsonpath='{.data.password}' | base64_decode)"
  login_osmo_with_token "${service_url}" "${default_admin_token}" || die "failed to log in to OSMO"

  backend_current="$(osmo config show BACKEND "${OSMO_BACKEND_NAME}")"
  printf '%s' "${backend_current}" | jq --arg grafana_url "${AMG_WORKSPACE_URL}" '.grafana_url = $grafana_url | .dashboard_url = (.dashboard_url // "")' >"${backend_config}"

  if ! osmo config update BACKEND "${OSMO_BACKEND_NAME}" \
    --file "${backend_config}" \
    --description "Configure AMG workspace for AWS managed observability" >"${update_log}" 2>&1; then
    cat "${update_log}" >&2
    log "OSMO backend config update returned non-zero; verifying effective config"
  fi

  osmo config show BACKEND "${OSMO_BACKEND_NAME}" | jq -e --arg grafana_url "${AMG_WORKSPACE_URL}" '.grafana_url == $grafana_url' >/dev/null

  if [[ -n "${port_forward_pid}" ]]; then
    kill "${port_forward_pid}" >/dev/null 2>&1 || true
  fi
  rm -f "${backend_config}" "${update_log}"
}

configure_kubectl

AWS_REGION="$(core_output aws_region)"
CLUSTER_NAME="$(core_output cluster_name)"
set_tf_var_default aws_region "${AWS_REGION}"
set_tf_var_default cluster_name "${CLUSTER_NAME}"
set_tf_var_default name_prefix "${CLUSTER_NAME}"
set_tf_var_default cluster_oidc_issuer_url "$(core_output cluster_oidc_issuer_url)"
set_tf_var_default cluster_oidc_provider_arn "$(core_output cluster_oidc_provider_arn)"
set_tf_var_default kube_context "$(kubectl config current-context)"

terraform -chdir="${OBSERVABILITY_TF_DIR}" init
terraform -chdir="${OBSERVABILITY_TF_DIR}" apply "$@"

AMP_WORKSPACE_ID="$(observability_output amp_workspace_id)"
AMP_PROMETHEUS_ENDPOINT="$(observability_output amp_prometheus_endpoint)"
AMG_WORKSPACE_ID="$(observability_output amg_workspace_id)"
AMG_WORKSPACE_URL="$(observability_output amg_workspace_url)"
GRAFANA_DATASOURCE_NAME="$(observability_output grafana_datasource_name)"
GRAFANA_DATASOURCE_UID="$(observability_output grafana_datasource_uid)"
GRAFANA_SERVICE_ACCOUNT_ID="$(observability_output grafana_provisioner_service_account_id)"

log "enabling OSMO PodMonitor resources"
enable_osmo_podmonitors

log "enabling DCGM exporter ServiceMonitor"
enable_dcgm_servicemonitor

log "provisioning AMG data source and dashboards"
GRAFANA_TOKEN="$(
  aws grafana create-workspace-service-account-token \
    --workspace-id "${AMG_WORKSPACE_ID}" \
    --service-account-id "${GRAFANA_SERVICE_ACCOUNT_ID}" \
    --name "deploy-observability-$(date +%s)" \
    --seconds-to-live 600 \
    --region "${AWS_REGION}" \
    --query serviceAccountToken.key \
    --output text
)"

upsert_grafana_datasource
for dashboard in "${OSMO_DASHBOARD_DIR}"/*.json; do
  import_dashboard "${dashboard}"
done
import_aws_osmo_overview_dashboard
unset GRAFANA_TOKEN

log "updating OSMO backend grafana_url"
update_osmo_backend_grafana_url

log "observability deployment complete: ${AMG_WORKSPACE_URL}"
