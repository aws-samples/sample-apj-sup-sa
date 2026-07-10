#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# setup_env.sh
#
# Creates (or refreshes) ./.venv at the project root, installs all Python
# dependencies needed by the notebooks and helper scripts, and registers the
# venv-scoped "python3" Jupyter kernel the notebooks bind to.
#
# Usage:
#   ./scripts/setup_env.sh             # fresh install, default Python 3.11+
#   ./scripts/setup_env.sh --recreate  # wipe and recreate .venv
#
# The script is idempotent: rerunning will only upgrade/install missing deps.
# -----------------------------------------------------------------------------
set -euo pipefail

# Resolve to project root (parent of scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"

RECREATE=0
for arg in "$@"; do
  case "$arg" in
    --recreate|-r) RECREATE=1 ;;
    --help|-h)
      sed -n '2,15p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

# ---------------------------------------------------------------------------
# 1. Locate a suitable Python (>= 3.11)
# ---------------------------------------------------------------------------
PY_BIN=""
for candidate in python3.12 python3.11 python3; do
  if command -v "${candidate}" >/dev/null 2>&1; then
    ver=$("${candidate}" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    major=$(echo "${ver}" | cut -d. -f1)
    minor=$(echo "${ver}" | cut -d. -f2)
    if [[ "${major}" -eq 3 && "${minor}" -ge 11 ]]; then
      PY_BIN="${candidate}"
      break
    fi
  fi
done

if [[ -z "${PY_BIN}" ]]; then
  echo "ERROR: Python >= 3.11 is required. Install it (e.g. via 'brew install python@3.12') and retry." >&2
  exit 1
fi
echo "Using Python: $(${PY_BIN} --version) at $(command -v ${PY_BIN})"

# ---------------------------------------------------------------------------
# 2. Create / recreate venv
# ---------------------------------------------------------------------------
if [[ "${RECREATE}" -eq 1 && -d "${VENV_DIR}" ]]; then
  echo "Removing existing venv at ${VENV_DIR}"
  rm -rf "${VENV_DIR}"
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "Creating venv at ${VENV_DIR}"
  "${PY_BIN}" -m venv "${VENV_DIR}"
fi

# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"

python -m pip install --upgrade pip setuptools wheel

# ---------------------------------------------------------------------------
# 3. Install project (editable) + all notebook & dev extras
# ---------------------------------------------------------------------------
# pyproject.toml declares the full dep set. The editable install lets changes
# in src/ take effect immediately; [notebook] pulls jupyterlab/llmeter/plotly,
# [dev] pulls pytest/ruff/mypy.
python -m pip install -e "${PROJECT_ROOT}[notebook,dev]"

# ---------------------------------------------------------------------------
# 4. Register Jupyter kernel
# ---------------------------------------------------------------------------
# Install the standard "python3" kernel INTO THE VENV (--prefix), not globally
# (--user). The notebooks request kernelspec name "python3", so this is the
# spec they bind to. Scoping it to the venv prefix means it points at this
# venv's interpreter and is discovered when start_jupyter.sh launches from the
# venv — and it vanishes with the venv instead of lingering in ~/Library/Jupyter
# as a stale global kernel pointing at an old repo path.
python -m ipykernel install --prefix "${VENV_DIR}"

echo ""
echo "------------------------------------------------------------------"
echo "Setup complete."
echo ""
echo "  venv:     ${VENV_DIR}"
echo "  kernel:   python3 (Python 3 (ipykernel)) — scoped to the venv"
echo ""
echo "Next steps:"
echo "  1. (Optional) Regenerate sample data (one-time, ~\$6.50 for 100K rows):"
echo "     source ${VENV_DIR}/bin/activate"
echo "     python ../sample-data/scripts/synthesize.py --per-seed 10000"
echo ""
echo "  2. Start Jupyter:"
echo "     ./scripts/start_jupyter.sh"
echo "------------------------------------------------------------------"
