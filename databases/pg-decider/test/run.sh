#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Starts a temporary cluster and the mock decider, then runs `make installcheck`.
# Used by test/Dockerfile; works locally too if initdb/pg_ctl/pg_config are on PATH.
set -euo pipefail
cd "$(dirname "$0")/.."
PGBIN="$(pg_config --bindir)"
export PGDATA="${PGDATA:-/tmp/pg-decider-data}"
export PGPORT="${PGPORT:-5498}"
export PGHOST=/tmp
export PGUSER="${PGUSER:-$(whoami)}"
unset DECIDER_API_KEY

if [ ! -f "$PGDATA/PG_VERSION" ]; then "$PGBIN/initdb" -D "$PGDATA" -U "$PGUSER" --auth=trust >/dev/null; fi
"$PGBIN/pg_ctl" -D "$PGDATA" -o "-p $PGPORT -k /tmp -c listen_addresses=''" -l "$PGDATA/log" -w start >/dev/null
python3 test/mock_decider.py 8766 & MOCK=$!
trap '"$PGBIN/pg_ctl" -D "$PGDATA" -m fast stop >/dev/null; kill $MOCK 2>/dev/null' EXIT
sleep 1

if make installcheck; then
  echo "decider: all regression tests passed"
else
  echo "decider: regression tests FAILED"; cat test/regression.diffs 2>/dev/null || true; exit 1
fi
