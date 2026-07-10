#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="${TF_DIR:-${ROOT_DIR}/infra/core}"
VERSIONS_FILE="${ROOT_DIR}/versions.yaml"

log() {
  printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

require_cmds() {
  local cmd
  for cmd in "$@"; do
    require_cmd "$cmd"
  done
}

terraform_output() {
  local key="$1"
  local env_key
  env_key="TF_OUTPUT_$(printf '%s' "${key}" | tr '[:lower:]' '[:upper:]')"

  if [[ -n "${!env_key:-}" ]]; then
    printf '%s' "${!env_key}"
    return 0
  fi

  terraform -chdir="${TF_DIR}" output -raw "${key}"
}

version_value() {
  local key="$1"
  local value
  value="$(awk -v key="${key}:" '$1 == key {gsub(/^"|"$/, "", $2); print $2; exit}' "${VERSIONS_FILE}")"
  [[ -n "${value}" ]] || die "version key not found in versions.yaml: ${key}"
  printf '%s' "${value}"
}

load_ngc_api_key() {
  local key_file="${NGC_API_KEY_FILE:-${HOME:-}/.nvidia}"
  local key_value=""

  if [[ -n "${NGC_API_KEY:-}" ]]; then
    export NGC_API_KEY
    return 0
  fi

  if [[ -r "${key_file}" ]]; then
    if grep -q '=' "${key_file}"; then
      key_value="$(
        awk -F= '
          $1 ~ /^(export[[:space:]]+)?NGC_API_KEY$/ ||
          $1 ~ /^(apikey|api_key)$/ {
            print $2
            exit
          }
        ' "${key_file}" | tr -d '[:space:]'
      )"
      if [[ -z "${key_value}" ]]; then
        key_value="$(awk -F= 'NF >= 2 {print $2; exit}' "${key_file}" | tr -d '[:space:]')"
      fi
    else
      key_value="$(tr -d '[:space:]' <"${key_file}")"
    fi
  fi

  [[ -n "${key_value}" ]] || die "NGC_API_KEY is required for nvcr.io image pulls. Export NGC_API_KEY or write the raw key to ${key_file}."

  NGC_API_KEY="${key_value}"
  export NGC_API_KEY
}

configure_kubectl() {
  local region cluster_name
  region="$(terraform_output aws_region)"
  cluster_name="$(terraform_output cluster_name)"
  aws eks update-kubeconfig --region "${region}" --name "${cluster_name}" >/dev/null
}

port_open() {
  local host="$1"
  local port="$2"
  (: <"/dev/tcp/${host}/${port}") >/dev/null 2>&1
}

base64_decode() {
  if base64 --decode </dev/null >/dev/null 2>&1; then
    base64 --decode
  else
    base64 -D
  fi
}

login_osmo_with_token() {
  local url="$1"
  local token="$2"
  local token_file
  token_file="$(mktemp)"
  chmod 600 "${token_file}"
  printf '%s' "${token}" >"${token_file}"

  if osmo login "${url}" --method=token --token-file "${token_file}" >/dev/null 2>&1; then
    rm -f "${token_file}"
    return 0
  fi

  if osmo login "${url}" --method=token --token "${token}" >/dev/null 2>&1; then
    rm -f "${token_file}"
    return 0
  fi

  rm -f "${token_file}"
  return 1
}
