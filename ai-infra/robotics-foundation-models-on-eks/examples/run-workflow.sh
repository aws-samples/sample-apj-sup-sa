#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=../scripts/common.sh
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/common.sh"

require_cmds aws kubectl jq terraform osmo

configure_kubectl

AWS_REGION="$(terraform_output aws_region)"
OSMO_NAMESPACE="$(terraform_output osmo_namespace)"
OSMO_RUNTIME_SECRET_ARN="$(terraform_output osmo_runtime_secret_arn)"
WORKFLOW_FILE="${WORKFLOW_FILE:-${ROOT_DIR}/examples/smoke/cpu-workflow/workflow.yaml}"
SMOKE_POOL="${SMOKE_POOL:-default}"
SMOKE_TIMEOUT_ATTEMPTS="${SMOKE_TIMEOUT_ATTEMPTS:-90}"
SMOKE_SET_NGC_CREDENTIAL="${SMOKE_SET_NGC_CREDENTIAL:-false}"
SMOKE_SET_HF_CREDENTIAL="${SMOKE_SET_HF_CREDENTIAL:-false}"
SMOKE_WORKFLOW_SET="${SMOKE_WORKFLOW_SET:-}"
SMOKE_WORKFLOW_SET_STRING="${SMOKE_WORKFLOW_SET_STRING:-}"
HF_TOKEN_FILE="${HF_TOKEN_FILE:-${HOME}/.huggingface/token}"

SECRET_JSON="$(aws secretsmanager get-secret-value \
  --region "${AWS_REGION}" \
  --secret-id "${OSMO_RUNTIME_SECRET_ARN}" \
  --query SecretString \
  --output text)"
DEFAULT_ADMIN_TOKEN="$(printf '%s' "${SECRET_JSON}" | jq -er '.default_admin_token')"

kubectl -n "${OSMO_NAMESPACE}" port-forward svc/osmo-service 9000:80 >/tmp/osmo-smoke-port-forward.log 2>&1 &
PORT_FORWARD_PID="$!"
# shellcheck disable=SC2329,SC2317
cleanup() {
  if [[ -n "${NGC_API_KEY_PAYLOAD_FILE:-}" ]]; then
    rm -f "${NGC_API_KEY_PAYLOAD_FILE}"
  fi
  if [[ -n "${HF_TOKEN_PAYLOAD_FILE:-}" ]]; then
    rm -f "${HF_TOKEN_PAYLOAD_FILE}"
  fi
  kill "${PORT_FORWARD_PID}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

for _ in $(seq 1 60); do
  if port_open 127.0.0.1 9000; then
    break
  fi
  sleep 2
done

port_open 127.0.0.1 9000 || die "OSMO service port-forward did not become ready"
login_osmo_with_token "http://127.0.0.1:9000" "${DEFAULT_ADMIN_TOKEN}" || die "failed to log in to OSMO"

if [[ "${SMOKE_SET_NGC_CREDENTIAL}" == "true" ]]; then
  load_ngc_api_key
  osmo credential set aws-osmo-ngc \
    --type REGISTRY \
    --payload registry=nvcr.io username="\$oauthtoken" auth="${NGC_API_KEY}" >/dev/null

  NGC_API_KEY_PAYLOAD_FILE="$(mktemp)"
  chmod 600 "${NGC_API_KEY_PAYLOAD_FILE}"
  printf '%s' "${NGC_API_KEY}" >"${NGC_API_KEY_PAYLOAD_FILE}"
  osmo credential delete ngc-api-key >/dev/null 2>&1 || true
  osmo credential set ngc-api-key \
    --type GENERIC \
    --payload-file key="${NGC_API_KEY_PAYLOAD_FILE}" >/dev/null
  rm -f "${NGC_API_KEY_PAYLOAD_FILE}"
  NGC_API_KEY_PAYLOAD_FILE=""
fi

if [[ "${SMOKE_SET_HF_CREDENTIAL}" == "true" ]]; then
  [[ -s "${HF_TOKEN_FILE}" ]] || die "Hugging Face token file not found or empty: ${HF_TOKEN_FILE}"
  HF_TOKEN_PAYLOAD_FILE="$(mktemp)"
  chmod 600 "${HF_TOKEN_PAYLOAD_FILE}"
  tr -d '[:space:]' <"${HF_TOKEN_FILE}" >"${HF_TOKEN_PAYLOAD_FILE}"
  osmo credential delete huggingface_token >/dev/null 2>&1 || true
  osmo credential set huggingface_token \
    --type GENERIC \
    --payload-file token="${HF_TOKEN_PAYLOAD_FILE}" >/dev/null
  rm -f "${HF_TOKEN_PAYLOAD_FILE}"
  HF_TOKEN_PAYLOAD_FILE=""
fi

WORKFLOW_START_TS="$(date -u +%s)"
SUBMIT_ARGS=(workflow submit "${WORKFLOW_FILE}" --pool "${SMOKE_POOL}" -t json)
if [[ -n "${SMOKE_WORKFLOW_SET}" ]]; then
  # shellcheck disable=SC2206
  SET_VALUES=(${SMOKE_WORKFLOW_SET})
  SUBMIT_ARGS+=(--set "${SET_VALUES[@]}")
fi
if [[ -n "${SMOKE_WORKFLOW_SET_STRING}" ]]; then
  # shellcheck disable=SC2206
  SET_STRING_VALUES=(${SMOKE_WORKFLOW_SET_STRING})
  SUBMIT_ARGS+=(--set-string "${SET_STRING_VALUES[@]}")
fi

if ! SUBMIT_OUTPUT="$(osmo "${SUBMIT_ARGS[@]}" 2>/tmp/osmo-smoke-submit.err)"; then
  cat /tmp/osmo-smoke-submit.err >&2
  die "failed to submit smoke workflow"
fi
WORKFLOW_ID="$(printf '%s' "${SUBMIT_OUTPUT}" | jq -er '.name // .id // .workflow_id // .workflowId' 2>/dev/null || true)"

[[ -n "${WORKFLOW_ID}" && "${WORKFLOW_ID}" != "null" ]] || {
  printf '%s\n' "${SUBMIT_OUTPUT}" >&2
  die "could not determine submitted workflow ID"
}

for _ in $(seq 1 "${SMOKE_TIMEOUT_ATTEMPTS}"); do
  QUERY_OUTPUT="$(osmo workflow query "${WORKFLOW_ID}" 2>/dev/null || true)"
  STATUS="$(printf '%s' "${QUERY_OUTPUT}" | awk -F: '/Status/ {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; exit}')"
  case "${STATUS}" in
    COMPLETED)
      WORKFLOW_END_TS="$(date -u +%s)"
      osmo workflow logs "${WORKFLOW_ID}" || true
      log "smoke workflow completed: ${WORKFLOW_ID}"
      log "smoke workflow runtime seconds: $((WORKFLOW_END_TS - WORKFLOW_START_TS))"
      exit 0
      ;;
    FAILED|FAILED_CANCELED|FAILED_CANCELLED|CANCELED|CANCELLED)
      printf '%s\n' "${QUERY_OUTPUT}" >&2
      osmo workflow logs "${WORKFLOW_ID}" || true
      die "smoke workflow ended with status ${STATUS}"
      ;;
  esac
  sleep 10
done

osmo workflow query "${WORKFLOW_ID}" || true
die "smoke workflow did not complete before timeout: ${WORKFLOW_ID}"
