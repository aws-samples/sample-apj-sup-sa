#!/usr/bin/env bash
# Activate venv + launch JupyterLab on 127.0.0.1:8888.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"

PORT=8888
for (( i = 1; i <= $#; i++ )); do
  case "${!i}" in
    --port)
      next=$((i + 1))
      PORT="${!next}"
      ;;
    --help|-h)
      sed -n '2,4p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
  esac
done

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "ERROR: venv not found. Run ./scripts/setup_env.sh first." >&2
  exit 1
fi

# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"

cd "${PROJECT_ROOT}"
echo "JupyterLab starting on 127.0.0.1:${PORT}"
echo "Open notebooks/<model>_batch.ipynb after it boots (e.g. qwen3_8b_batch.ipynb)."
exec jupyter lab \
  --no-browser \
  --ip=127.0.0.1 \
  --port="${PORT}" \
  --ServerApp.root_dir="${PROJECT_ROOT}"
