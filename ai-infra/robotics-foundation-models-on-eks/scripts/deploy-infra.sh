#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=./scripts/common.sh
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

require_cmds aws curl terraform

arg_sets_cluster_endpoint_cidrs() {
  local arg
  for arg in "$@"; do
    if [[ "${arg}" == *cluster_endpoint_public_access_cidrs* ]]; then
      return 0
    fi
  done
  return 1
}

hcl_cidr_list() {
  local cidrs="$1"
  local raw cidr sep=""
  local output="["

  IFS=',' read -r -a parts <<<"${cidrs}"
  for raw in "${parts[@]}"; do
    cidr="${raw#"${raw%%[![:space:]]*}"}"
    cidr="${cidr%"${cidr##*[![:space:]]}"}"
    [[ -n "${cidr}" ]] || continue

    if [[ "${cidr}" == *\"* || "${cidr}" == *"'"* ]]; then
      die "CIDR values must not contain quotes: ${cidr}"
    fi
    if [[ "${cidr}" == "0.0.0.0/0" || "${cidr}" == "::/0" ]]; then
      die "do not expose the EKS API endpoint to ${cidr}"
    fi

    output+="${sep}\"${cidr}\""
    sep=", "
  done

  [[ "${output}" != "[" ]] || die "no CIDRs were provided"
  output+="]"
  printf '%s' "${output}"
}

terraform_public_endpoint_args=()
if arg_sets_cluster_endpoint_cidrs "$@"; then
  log "using cluster_endpoint_public_access_cidrs from Terraform CLI arguments"
elif [[ -n "${TF_VAR_cluster_endpoint_public_access_cidrs:-}" ]]; then
  if [[ "${TF_VAR_cluster_endpoint_public_access_cidrs}" == \[* ]]; then
    endpoint_cidrs_value="${TF_VAR_cluster_endpoint_public_access_cidrs}"
  else
    endpoint_cidrs_value="$(hcl_cidr_list "${TF_VAR_cluster_endpoint_public_access_cidrs}")"
  fi
  terraform_public_endpoint_args=(-var "cluster_endpoint_public_access_cidrs=${endpoint_cidrs_value}")
  log "using cluster_endpoint_public_access_cidrs from TF_VAR_cluster_endpoint_public_access_cidrs"
else
  endpoint_cidrs="${CLUSTER_ENDPOINT_PUBLIC_ACCESS_CIDRS:-auto}"
  case "${endpoint_cidrs}" in
    auto)
      caller_ip="$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')"
      if [[ ! "${caller_ip}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
        die "could not detect caller public IPv4 address; set CLUSTER_ENDPOINT_PUBLIC_ACCESS_CIDRS or pass -var=cluster_endpoint_public_access_cidrs=[...]"
      fi
      endpoint_cidrs="${caller_ip}/32"
      terraform_public_endpoint_args=(-var "cluster_endpoint_public_access_cidrs=$(hcl_cidr_list "${endpoint_cidrs}")")
      log "allowing public EKS API endpoint access from ${endpoint_cidrs}"
      ;;
    "" | "[]" | private | private-only | none | disabled)
      terraform_public_endpoint_args=(-var "cluster_endpoint_public_access_cidrs=[]")
      log "leaving the EKS API endpoint private-only"
      ;;
    *)
      terraform_public_endpoint_args=(-var "cluster_endpoint_public_access_cidrs=$(hcl_cidr_list "${endpoint_cidrs}")")
      log "allowing public EKS API endpoint access from ${endpoint_cidrs}"
      ;;
  esac
fi

terraform -chdir="${TF_DIR}" init -input=false
terraform -chdir="${TF_DIR}" apply "${terraform_public_endpoint_args[@]}" "$@"
