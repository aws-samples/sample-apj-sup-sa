#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILED="false"

while IFS= read -r -d '' file; do
  if ! LC_ALL=C grep -Iq . "${file}"; then
    continue
  fi

  set +e
  awk '
    /^[[:space:]]*cluster_endpoint_public_access_cidrs[[:space:]]*=/ && /0\.0\.0\.0\/0|::\/0/ {
      print FILENAME ":" FNR ": public EKS endpoint CIDR"
      found = 1
    }

    /ingress[[:space:]]*\{/ {
      in_ingress = 1
      start = FNR
      block = $0
      next
    }

    in_ingress {
      block = block "\n" $0
      if ($0 ~ /^[[:space:]]*}/) {
        if (block ~ /0\.0\.0\.0\/0|::\/0/) {
          print FILENAME ":" start ": public CIDR in ingress block"
          found = 1
        }
        in_ingress = 0
        block = ""
      }
    }

    END {
      exit found ? 2 : 0
    }
  ' "${file}"
  status="$?"
  set -e

  if [[ "${status}" == "2" ]]; then
    FAILED="true"
  elif [[ "${status}" != "0" ]]; then
    exit "${status}"
  fi
done < <(find "${ROOT_DIR}/infra" "${ROOT_DIR}/scripts" -type f \
  ! -path '*/.terraform/*' \
  ! -name 'terraform.tfvars.example' \
  -print0)

if [[ "${FAILED}" == "true" ]]; then
  printf 'error: public ingress scan failed\n' >&2
  exit 1
fi
