#!/usr/bin/env bash
# F2: RATE of simulator calls fail for DUR seconds. Retries absorb most; ticks keep moving; no allocation may fail.
source "$(dirname "$0")/lib.sh"
RATE=${1:-0.5}; DUR=${2:-60}
precondition || exit 1
f0=$(allocation_failures)
fault error_rate "$DUR" "{\"rate\":$RATE}"
t0=$(now)
det=$(wait_until '.components.fuel_simulator.status != "Healthy"' 30)
tick0=$(st '.data_tick'); sleep 15; tick1=$(st '.data_tick')
wait_fault_end "$t0" "$DUR"
rec=$(wait_until "$healthy" 60)
f1=$(allocation_failures)
ok=0; [ "$f0" = "$f1" ] && [ "$tick1" -gt "$tick0" ] && ok=1
record F2 "Simulator error rate ${RATE} for ${DUR}s" "$det" \
  "retries + last-good per resource; ticks advanced $tick0->$tick1; failed allocations $f0->$f1" \
  "$rec" "$(verdict "$det" "$rec" "$ok")" "recover counted from fault expiry"
