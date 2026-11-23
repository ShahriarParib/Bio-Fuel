#!/usr/bin/env bash
# F3: every simulator call is delayed DELAY ms (default 2500, above the 2 s client timeout).
source "$(dirname "$0")/lib.sh"
DELAY=${1:-2500}; DUR=${2:-45}
precondition || exit 1
fault latency "$DUR" "{\"delay_ms\":$DELAY}"
t0=$(now)
det=$(wait_until '.components.fuel_simulator.status != "Healthy"' 40)
seen=$(st '.components.fuel_simulator.detail')
wait_fault_end "$t0" "$DUR"
rec=$(wait_until "$healthy" 60)
record F3 "Simulator latency ${DELAY}ms for ${DUR}s" "$det" \
  "2 s timeout counts as failure; nothing written on old data ($seen)" \
  "$rec" "$(verdict "$det" "$rec")" "recover counted from fault expiry"
