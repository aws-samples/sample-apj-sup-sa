#!/usr/bin/env bash
# Inject or clear a fault in the payments service.
#   ./chaos.sh latency 2500      # +2.5s on every payments call
#   ./chaos.sh errors 0.3        # 30% of payments calls return 503
#   ./chaos.sh both 2500 0.3
#   ./chaos.sh off
#   ./chaos.sh status
set -euo pipefail
URL="${PAYMENTS_URL:-http://localhost:8002}"
case "${1:-status}" in
  latency) curl -fsS -X POST "$URL/chaos" -H 'content-type: application/json' -d "{\"latency_ms\": ${2:-2500}, \"error_rate\": 0}" ;;
  errors)  curl -fsS -X POST "$URL/chaos" -H 'content-type: application/json' -d "{\"latency_ms\": 0, \"error_rate\": ${2:-0.3}}" ;;
  both)    curl -fsS -X POST "$URL/chaos" -H 'content-type: application/json' -d "{\"latency_ms\": ${2:-2500}, \"error_rate\": ${3:-0.3}}" ;;
  off)     curl -fsS -X DELETE "$URL/chaos" ;;
  status)  curl -fsS "$URL/healthz" ;;
  *) echo "usage: $0 latency <ms> | errors <rate> | both <ms> <rate> | off | status"; exit 1 ;;
esac
echo
