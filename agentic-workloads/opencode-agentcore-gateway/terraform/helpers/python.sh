#!/bin/sh
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Runs a Terraform helper (acgw.py, gateway_rule.py) with a Python chosen when the command runs, never one stored in
# Terraform state: destroy steps run the command saved at apply time, so a path picked then would break a removal
# applied from another checkout or machine. The choice, first that fits: the repo's .venv, then python3 from PATH,
# then uv with the helpers' requirements (in a cached environment uv reuses). A .venv or python3 fits when it has
# boto3 >= agentcore_wait.MIN_BOTO3 (the gateway-rule fields) and requests; an older one is passed over rather than failing later with
# "Unknown parameter matchModels". A suitable local Python is preferred to uv, so an offline machine still works.
# Paths are quoted throughout, so a checkout under a folder with a space still works.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
# The boto3 minimum has one home, agentcore_wait.MIN_BOTO3 (the helpers check it again at import).
min=$(sed -n 's/^MIN_BOTO3 = (\([0-9]*\), \([0-9]*\), \([0-9]*\)).*/\1.\2.\3/p' "$here/agentcore_wait.py")
if [ -z "$min" ]; then
  echo "python.sh: MIN_BOTO3 not found in $here/agentcore_wait.py" >&2
  exit 1
fi
fit() {  # reads the installed versions without importing boto3 (about a second saved on every helper call)
  "$1" -c 'import sys, importlib.metadata as m, importlib.util as u
v = tuple(int(x) for x in m.version("boto3").split(".")[:3])
sys.exit(v < tuple(int(x) for x in sys.argv[1].split(".")) or u.find_spec("requests") is None)' "$min" >/dev/null 2>&1
}
if [ -x "$repo/.venv/bin/python" ] && fit "$repo/.venv/bin/python"; then
  exec "$repo/.venv/bin/python" "$@"
elif command -v python3 >/dev/null 2>&1 && fit python3; then
  exec python3 "$@"
elif command -v uv >/dev/null 2>&1; then
  # Pinned as tests/requirements.txt pins them (test_python_sh_pins_match): the versions the suite last ran with.
  # Their dependencies (botocore, urllib3 and the rest) resolve as of --exclude-newer, not to whatever is newest when
  # the plan runs, and only from wheels (--no-build: no sdist's build code runs with the operator's AWS credentials).
  # Move the date with each pin change.
  exec uv run --no-project --no-build --exclude-newer "2026-11-24T00:00:00Z" --with "boto3==1.44.1" --with "requests==2.34.2" python "$@"
else
  echo "python.sh: no Python with boto3 >= $min and requests: install uv, or create ../.venv with them" >&2
  exit 1
fi
