#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=../../../scripts/common.sh
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/scripts/common.sh"

require_cmds aws curl jq kubectl osmo terraform

EXAMPLE_DIR="${ROOT_DIR}/examples/simulation/nut-pouring-pipeline"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
NUT_POURING_WORKFLOWS_DIR="${NUT_POURING_WORKFLOWS_DIR:-${EXAMPLE_DIR}/workflows}"
OSMO_GPU_PLATFORM_NAME="${OSMO_GPU_PLATFORM_NAME:-g7e-rtx-pro-6000}"
NUT_POURING_POOL="${NUT_POURING_POOL:-default}"
NUT_POURING_WORK_DIR="${NUT_POURING_WORK_DIR:-$(mktemp -d)}"
NUT_POURING_KEEP_WORK_DIR="${NUT_POURING_KEEP_WORK_DIR:-false}"
NUT_POURING_INPUT_DATASET="${NUT_POURING_INPUT_DATASET:-PhysAI-InputMimic}"
NUT_POURING_DATASET_BUCKET="${NUT_POURING_DATASET_BUCKET:-aws-osmo}"
NUT_POURING_DATASET_URL="${NUT_POURING_DATASET_URL:-https://download.isaacsim.omniverse.nvidia.com/isaaclab/dataset/dataset_annotated_gr1_nut_pouring.hdf5}"
NUT_POURING_SKIP_DATASET_UPLOAD="${NUT_POURING_SKIP_DATASET_UPLOAD:-false}"
NUT_POURING_PREWARM_GPU_NODE="${NUT_POURING_PREWARM_GPU_NODE:-true}"
NUT_POURING_PREWARM_INSTANCE_TYPE="${NUT_POURING_PREWARM_INSTANCE_TYPE:-g7e.24xlarge}"
NUT_POURING_RETAIN_PREWARM_POD="${NUT_POURING_RETAIN_PREWARM_POD:-false}"
NUT_POURING_VERIFY_GPU_CLEANUP="${NUT_POURING_VERIFY_GPU_CLEANUP:-true}"
NUT_POURING_CAPACITY_WAIT_ATTEMPTS="${NUT_POURING_CAPACITY_WAIT_ATTEMPTS:-90}"
NUT_POURING_CAPACITY_WAIT_SECONDS="${NUT_POURING_CAPACITY_WAIT_SECONDS:-10}"
NUT_POURING_REQUIRED_CPU="${NUT_POURING_REQUIRED_CPU:-64}"
NUT_POURING_REQUIRED_MEMORY_GI="${NUT_POURING_REQUIRED_MEMORY_GI:-512}"
NUT_POURING_REQUIRED_GPU="${NUT_POURING_REQUIRED_GPU:-1}"
NUT_POURING_MAX_DEMOS="${NUT_POURING_MAX_DEMOS:-}"
NUT_POURING_START_STEP="${NUT_POURING_START_STEP:-1}"
NUT_POURING_WAIT_ATTEMPTS="${NUT_POURING_WAIT_ATTEMPTS:-4320}"
NUT_POURING_WAIT_SECONDS="${NUT_POURING_WAIT_SECONDS:-60}"
NUT_POURING_LOG_LINES="${NUT_POURING_LOG_LINES:-200}"
HF_TOKEN_FILE="${HF_TOKEN_FILE:-}"
HF_TOKEN_PAYLOAD_FILE=""
kubectl_base=(kubectl)
if [[ -n "${KUBE_CONTEXT}" ]]; then
  kubectl_base=(kubectl --context "${KUBE_CONTEXT}")
fi

WORKFLOWS=(
  01_mimic_generation.yaml
  02_hdf5_to_mp4.yaml
  03_cosmos_augmentation.yaml
  04_mp4_to_hdf5.yaml
  05_lerobot_conversion.yaml
  06_groot_finetune.yaml
)

if [[ -n "${NUT_POURING_MAX_DEMOS}" && ! "${NUT_POURING_MAX_DEMOS}" =~ ^[0-9]+$ ]]; then
  die "NUT_POURING_MAX_DEMOS must be an integer when set"
fi

cleanup() {
  [[ -n "${HF_TOKEN_PAYLOAD_FILE}" ]] && rm -f "${HF_TOKEN_PAYLOAD_FILE}"
  if [[ "${NUT_POURING_KEEP_WORK_DIR}" != "true" ]]; then
    rm -rf "${NUT_POURING_WORK_DIR}"
  fi
  if [[ "${NUT_POURING_PREWARM_GPU_NODE}" == "true" && "${NUT_POURING_RETAIN_PREWARM_POD}" != "true" ]]; then
    "${kubectl_base[@]}" -n "$(terraform_output osmo_workload_namespace)" delete pod aws-osmo-gpu-prewarm \
      --ignore-not-found >/dev/null 2>&1 || true
  fi
  if [[ -n "${PORT_FORWARD_PID:-}" ]]; then
    kill "${PORT_FORWARD_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

hf_token() {
  if [[ -n "${HF_TOKEN:-}" ]]; then
    printf '%s' "${HF_TOKEN}"
  elif [[ -n "${HUGGING_FACE_HUB_TOKEN:-}" ]]; then
    printf '%s' "${HUGGING_FACE_HUB_TOKEN}"
  elif [[ -n "${HF_TOKEN_FILE}" && -r "${HF_TOKEN_FILE}" ]]; then
    tr -d '[:space:]' <"${HF_TOKEN_FILE}"
  else
    die "HF_TOKEN is required. Export HF_TOKEN or set HF_TOKEN_FILE to a readable token file."
  fi
}

set_osmo_credential() {
  local credential_name="$1"
  shift

  osmo credential delete "${credential_name}" >/dev/null 2>&1 || true
  osmo credential set "${credential_name}" "$@" >/dev/null
}

workflow_id_from_submit() {
  jq -er '.name // .id // .workflow_id // .workflowId' 2>/dev/null || true
}

show_workflow_logs() {
  osmo workflow logs "$1" 2>/dev/null | tail -n "${NUT_POURING_LOG_LINES}" || true
}

wait_workflow() {
  local workflow_id="$1"
  local query_output status

  for _ in $(seq 1 "${NUT_POURING_WAIT_ATTEMPTS}"); do
    query_output="$(osmo workflow query "${workflow_id}" 2>/dev/null || true)"
    status="$(printf '%s' "${query_output}" | awk -F: '/Status/ {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; exit}')"
    case "${status}" in
      COMPLETED)
        show_workflow_logs "${workflow_id}"
        return 0
        ;;
      FAILED|FAILED_*|CANCELED|CANCELLED)
        printf '%s\n' "${query_output}" >&2
        show_workflow_logs "${workflow_id}" >&2
        die "nut pouring workflow ${workflow_id} ended with status ${status}"
        ;;
    esac
    sleep "${NUT_POURING_WAIT_SECONDS}"
  done

  osmo workflow query "${workflow_id}" || true
  die "nut pouring workflow did not complete before timeout: ${workflow_id}"
}

expected_output_dataset() {
  case "$1" in
    01_mimic_generation.yaml) printf '%s\n' "PhysAI-MimicGen" ;;
    02_hdf5_to_mp4.yaml) printf '%s\n' "PhysAI-MP4Videos" ;;
    03_cosmos_augmentation.yaml) printf '%s\n' "PhysAI-CosmosAugmentedMP4" ;;
    04_mp4_to_hdf5.yaml) printf '%s\n' "PhysAI-CosmosAugmentedHDF5" ;;
    05_lerobot_conversion.yaml) printf '%s\n' "PhysAI-LeRobotDataset" ;;
    06_groot_finetune.yaml) printf '%s\n' "PhysAI-GR00T-Finetuned" ;;
  esac
}

verify_dataset_ready() {
  local dataset_name="$1"
  local dataset_json

  log "verifying output dataset ${dataset_name}"
  dataset_json="$(osmo dataset info "${dataset_name}" -t json 2>/dev/null || true)"
  if ! printf '%s' "${dataset_json}" | jq -e '
      any(.versions[]?; .status == "READY" and ((.size // 0) | tonumber) > 0)
    ' >/dev/null; then
    printf '%s\n' "${dataset_json}" >&2
    die "expected output dataset is missing or empty: ${dataset_name}"
  fi
}

submit_and_wait() {
  local workflow_file="$1"
  local expected_dataset="${2:-}"
  local start_epoch end_epoch submit_output workflow_id

  log "submitting $(basename "${workflow_file}")"
  start_epoch="$(date -u +%s)"
  if ! submit_output="$(osmo workflow submit "${workflow_file}" --pool "${NUT_POURING_POOL}" -t json 2>/tmp/osmo-nut-pouring-submit.err)"; then
    cat /tmp/osmo-nut-pouring-submit.err >&2
    die "failed to submit $(basename "${workflow_file}")"
  fi

  workflow_id="$(printf '%s' "${submit_output}" | workflow_id_from_submit)"
  [[ -n "${workflow_id}" && "${workflow_id}" != "null" ]] || {
    printf '%s\n' "${submit_output}" >&2
    die "could not determine workflow ID for $(basename "${workflow_file}")"
  }

  printf '%s\t%s\n' "$(basename "${workflow_file}")" "${workflow_id}" | tee -a "${NUT_POURING_WORK_DIR}/workflow-ids.tsv"
  wait_workflow "${workflow_id}"
  end_epoch="$(date -u +%s)"
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "$(basename "${workflow_file}")" "${workflow_id}" "${start_epoch}" "${end_epoch}" "$((end_epoch - start_epoch))" \
    >>"${NUT_POURING_WORK_DIR}/workflow-runtimes.tsv"
  [[ -z "${expected_dataset}" ]] || verify_dataset_ready "${expected_dataset}"
  log "completed $(basename "${workflow_file}"): ${workflow_id}"
}

wait_osmo_gpu_capacity() {
  local ready=""

  for _ in $(seq 1 "${NUT_POURING_CAPACITY_WAIT_ATTEMPTS}"); do
    ready="$(
      osmo resource list --pool "${NUT_POURING_POOL}" --platform "${OSMO_GPU_PLATFORM_NAME}" -t json 2>/dev/null | jq -er \
        --arg platform "${OSMO_GPU_PLATFORM_NAME}" \
        --argjson cpu "${NUT_POURING_REQUIRED_CPU}" \
        --argjson memory "${NUT_POURING_REQUIRED_MEMORY_GI}" \
        --argjson gpu "${NUT_POURING_REQUIRED_GPU}" \
        '.resources[]?
         | select(any(.["exposed_fields"]["pool/platform"][]?; test("/" + $platform + "$")))
         | select((.exposed_fields.cpu | tonumber) >= $cpu)
         | select((.exposed_fields.memory | tonumber) >= $memory)
         | select((.exposed_fields.gpu | tonumber) >= $gpu)
         | .hostname' | head -n 1
    )" || true

    [[ -z "${ready}" ]] || {
      log "OSMO sees G7e capacity for nut pouring on ${ready}"
      return 0
    }
    sleep "${NUT_POURING_CAPACITY_WAIT_SECONDS}"
  done

  osmo resource list --all || true
  die "OSMO did not observe G7e capacity for nut pouring before timeout"
}

copy_workflows() {
  local target_dir="${NUT_POURING_WORK_DIR}/workflows"

  [[ -d "${NUT_POURING_WORKFLOWS_DIR}" ]] || die "missing workflow directory: ${NUT_POURING_WORKFLOWS_DIR}"
  rm -rf "${target_dir}"
  mkdir -p "${target_dir}"
  cp "${NUT_POURING_WORKFLOWS_DIR}"/*.yaml "${target_dir}/"
  if [[ "${NUT_POURING_INPUT_DATASET}" != "PhysAI-InputMimic" ]]; then
    log "rewriting input dataset references to ${NUT_POURING_INPUT_DATASET}"
    for workflow in "${target_dir}"/*.yaml; do
      awk -v dataset="${NUT_POURING_INPUT_DATASET}" '{ gsub(/PhysAI-InputMimic/, dataset); print }' \
        "${workflow}" >"${workflow}.tmp"
      mv "${workflow}.tmp" "${workflow}"
    done
  fi
  if [[ -n "${NUT_POURING_MAX_DEMOS}" ]]; then
    log "limiting Cosmos augmentation to ${NUT_POURING_MAX_DEMOS} RGB MP4 files"
    awk -v max_demos="${NUT_POURING_MAX_DEMOS}" '
      /MP4_COUNT=\$\(wc -l < "\$MP4_LIST_FILE"\)/ {
        print "          head -n " max_demos " \"$MP4_LIST_FILE\" > \"${MP4_LIST_FILE}.limited\""
        print "          mv \"${MP4_LIST_FILE}.limited\" \"$MP4_LIST_FILE\""
      }
      { print }
    ' "${target_dir}/03_cosmos_augmentation.yaml" >"${target_dir}/03_cosmos_augmentation.yaml.tmp"
    mv "${target_dir}/03_cosmos_augmentation.yaml.tmp" "${target_dir}/03_cosmos_augmentation.yaml"
  fi
  printf '%s\n' "${target_dir}"
}

if [[ -n "${KUBE_CONTEXT}" ]]; then
  log "using existing Kubernetes context ${KUBE_CONTEXT}"
else
  configure_kubectl
fi

AWS_REGION="$(terraform_output aws_region)"
OSMO_NAMESPACE="$(terraform_output osmo_namespace)"
OSMO_RUNTIME_SECRET_ARN="$(terraform_output osmo_runtime_secret_arn)"

SECRET_JSON="$(aws secretsmanager get-secret-value \
  --region "${AWS_REGION}" \
  --secret-id "${OSMO_RUNTIME_SECRET_ARN}" \
  --query SecretString \
  --output text)"
DEFAULT_ADMIN_TOKEN="$(printf '%s' "${SECRET_JSON}" | jq -er '.default_admin_token')"

"${kubectl_base[@]}" -n "${OSMO_NAMESPACE}" port-forward svc/osmo-service 9000:80 >/tmp/osmo-nut-pouring-port-forward.log 2>&1 &
PORT_FORWARD_PID="$!"

for _ in $(seq 1 60); do
  port_open 127.0.0.1 9000 && break
  sleep 2
done

port_open 127.0.0.1 9000 || die "OSMO service port-forward did not become ready"
login_osmo_with_token "http://127.0.0.1:9000" "${DEFAULT_ADMIN_TOKEN}" || die "failed to log in to OSMO"
osmo profile set bucket "${NUT_POURING_DATASET_BUCKET}" >/dev/null

load_ngc_api_key
log "setting OSMO NGC registry credential"
set_osmo_credential aws-osmo-ngc \
  --type REGISTRY \
  --payload registry=nvcr.io username="\$oauthtoken" auth="${NGC_API_KEY}"

log "setting OSMO Hugging Face generic credential"
HF_TOKEN_PAYLOAD_FILE="$(mktemp)"
chmod 600 "${HF_TOKEN_PAYLOAD_FILE}"
hf_token >"${HF_TOKEN_PAYLOAD_FILE}"
set_osmo_credential huggingface_token \
  --type GENERIC \
  --payload-file token="${HF_TOKEN_PAYLOAD_FILE}"
rm -f "${HF_TOKEN_PAYLOAD_FILE}"
HF_TOKEN_PAYLOAD_FILE=""

if [[ "${NUT_POURING_PREWARM_GPU_NODE}" == "true" ]]; then
  GPU_PREWARM_INSTANCE_TYPE="${NUT_POURING_PREWARM_INSTANCE_TYPE}" \
    "${ROOT_DIR}/infra/kubernetes/prewarm-gpu-node.sh"
  wait_osmo_gpu_capacity
fi

mkdir -p "${NUT_POURING_WORK_DIR}"
PREPARED_WORKFLOWS_DIR="$(copy_workflows)"

if [[ "${NUT_POURING_SKIP_DATASET_UPLOAD}" != "true" ]]; then
  DATASET_FILE="${NUT_POURING_WORK_DIR}/dataset_annotated_gr1_nut_pouring.hdf5"
  log "downloading nut pouring input dataset"
  curl -fL --retry 3 -o "${DATASET_FILE}" "${NUT_POURING_DATASET_URL}"
  log "uploading input dataset ${NUT_POURING_INPUT_DATASET}"
  osmo dataset upload "${NUT_POURING_INPUT_DATASET}" "${DATASET_FILE}"
fi

if [[ "${NUT_POURING_START_STEP}" -le 1 ]]; then
  : >"${NUT_POURING_WORK_DIR}/workflow-ids.tsv"
  : >"${NUT_POURING_WORK_DIR}/workflow-runtimes.tsv"
else
  touch "${NUT_POURING_WORK_DIR}/workflow-ids.tsv"
  touch "${NUT_POURING_WORK_DIR}/workflow-runtimes.tsv"
fi

for workflow in "${WORKFLOWS[@]}"; do
  step="${workflow%%_*}"
  step_number="$((10#${step}))"
  if [[ "${step_number}" -lt "${NUT_POURING_START_STEP}" ]]; then
    log "skipping ${workflow} because NUT_POURING_START_STEP=${NUT_POURING_START_STEP}"
    continue
  fi
  submit_and_wait "${PREPARED_WORKFLOWS_DIR}/${workflow}" "$(expected_output_dataset "${workflow}")"
done

log "nut pouring workflow IDs"
cat "${NUT_POURING_WORK_DIR}/workflow-ids.tsv"

if [[ "${NUT_POURING_PREWARM_GPU_NODE}" == "true" && "${NUT_POURING_VERIFY_GPU_CLEANUP}" == "true" ]]; then
  log "verifying Karpenter GPU node cleanup"
  "${ROOT_DIR}/infra/kubernetes/wait-gpu-node-cleanup.sh"
fi
