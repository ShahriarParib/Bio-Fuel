#!/usr/bin/env bash
# Health-check every service via host ports (reads .env if present). Exit 0 = all healthy.
set -uo pipefail
[ -f .env ] && set -a && . ./.env && set +a
WAIT="${SMOKE_TIMEOUT:-120}"
CHECKS=(
  "simulator|http://localhost:${SIM_HOST_PORT:-8000}/v1/health"
  "engine|http://localhost:${ENGINE_HOST_PORT:-8100}/health/live"
  "engine-ready|http://localhost:${ENGINE_HOST_PORT:-8100}/health/ready"
  "intelligence|http://localhost:${INTEL_HOST_PORT:-8200}/health/live"
  "api|http://localhost:${API_HOST_PORT:-18080}/health/live"
  "frontend|http://localhost:${FE_HOST_PORT:-3000}/"
  "frontend-api|http://localhost:${FE_HOST_PORT:-3000}/api/ping"
  "system-status|http://localhost:${FE_HOST_PORT:-3000}/api/system/status"
)
fail=0
printf "%-14s %-6s %s\n" SERVICE STATUS URL
for entry in "${CHECKS[@]}"; do
  name="${entry%%|*}"; url="${entry#*|}"
  ok=0; end=$((SECONDS + WAIT))
  while [ $SECONDS -lt $end ]; do
    if curl -sf -m 3 "$url" >/dev/null; then ok=1; break; fi
    sleep 2
  done
  if [ $ok = 1 ]; then status=OK; else status=FAIL; fail=1; fi
  printf "%-14s %-6s %s\n" "$name" "$status" "$url"
  WAIT=15   # only the first check waits for the full stack boot
done
if [ $fail = 0 ]; then
  overall=$(curl -s "http://localhost:${FE_HOST_PORT:-3000}/api/system/status" | python3 -c 'import json,sys;print(json.load(sys.stdin)["overall"])')
  echo "overall system status: $overall"
  echo "SMOKE OK"
else
  echo "SMOKE FAILED"; exit 1
fi
