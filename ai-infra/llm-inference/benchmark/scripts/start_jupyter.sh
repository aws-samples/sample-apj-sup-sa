#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# start_jupyter.sh
#
# Activates the project venv and starts JupyterLab on 127.0.0.1:8888.
# Prints the URL with access token so the user can open it in a browser.
#
# Usage:
#   ./scripts/start_jupyter.sh              # default port 8888
#   ./scripts/start_jupyter.sh --port 8899  # custom port
# -----------------------------------------------------------------------------
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
      sed -n '2,12p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
  esac
done

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "ERROR: venv not found at ${VENV_DIR}. Run ./scripts/setup_env.sh first." >&2
  exit 1
fi

# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"

if ! python -c "import jupyterlab" >/dev/null 2>&1; then
  echo "ERROR: jupyterlab is not installed in the venv. Run ./scripts/setup_env.sh." >&2
  exit 1
fi

cat <<EOF
------------------------------------------------------------------
Starting JupyterLab

  project root: ${PROJECT_ROOT}
  bind:         127.0.0.1:${PORT}
  kernel to use inside the notebook: "Python 3 (ipykernel)"

When JupyterLab finishes starting, look below for a line like:

    http://127.0.0.1:${PORT}/lab?token=...

Copy that URL into your browser, then open one of:

    models/qwen3_8b/qwen3-8b-vllm-ec2-benchmark.ipynb
    models/mistral_small_3_2_24b/mistral-small-3-2-24b-vllm-ec2-benchmark.ipynb
    models/qwen3_30b_a3b/qwen3-30b-a3b-vllm-ec2-benchmark.ipynb
    models/gemma_4_31b/gemma-4-31b-vllm-ec2-benchmark.ipynb
    models/medgemma_27b/medgemma-27b-vllm-ec2-benchmark.ipynb
    models/llama_4_scout_17b/llama-4-scout-17b-vllm-ec2-benchmark.ipynb

Press Ctrl-C in this terminal to shut JupyterLab down.
------------------------------------------------------------------
EOF

cd "${PROJECT_ROOT}"
exec jupyter lab \
  --no-browser \
  --ip=127.0.0.1 \
  --port="${PORT}" \
  --ServerApp.root_dir="${PROJECT_ROOT}"
