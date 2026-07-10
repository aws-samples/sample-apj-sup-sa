#!/usr/bin/env bash
# Create ./.venv, install dev + notebook extras, register the venv-scoped
# "python3" Jupyter kernel the notebooks bind to.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"

cd "${PROJECT_ROOT}"

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "Creating venv at ${VENV_DIR}..."
  python3.11 -m venv "${VENV_DIR}" || python3 -m venv "${VENV_DIR}"
fi

# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"

pip install --upgrade pip
pip install -e '.[dev,notebook]'

# Register the standard "python3" kernel INTO THE VENV (--prefix), not
# globally (--user). The notebooks request kernelspec name "python3", so this
# is the spec they bind to. Scoping it to the venv prefix means it points at
# this venv's interpreter and is discovered when start_jupyter.sh launches
# from the venv — and it vanishes with the venv instead of lingering in
# ~/Library/Jupyter as a stale global kernel pointing at an old repo path.
python -m ipykernel install --prefix "${VENV_DIR}"

echo ""
echo "Environment ready."
echo "  venv:   ${VENV_DIR}"
echo "  kernel: python3 (Python 3 (ipykernel)) — scoped to the venv"
echo ""
echo "Next steps:"
echo "  source ${VENV_DIR}/bin/activate"
echo "  pytest tests/"
echo "  ./scripts/start_jupyter.sh"
