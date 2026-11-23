#!/usr/bin/env bash
# F1: the simulator returns 503 on every call for DUR seconds (default 75, longer than the 60 s SAFE_HOLD threshold).
source "$(dirname "$0")/lib.sh"
DUR=${1:-75}
precondition || exit 1
fault unavailable "$DUR"
t0=$(now)
det=$(wait_until '.components.fuel_simulator.status == "Down"' 30)
hold=$(wait_until '.engine_mode == "SAFE_HOLD"' 90)
code=$(curl -s -o /dev/null -w '%{http_code}' "$API/state")
age=$(st '.data_age_seconds')
wait_fault_end "$t0" "$DUR"
rec=$(wait_until "$healthy" 60)
ok=0; [ "$hold" != "timeout" ] && [ "$code" = "200" ] && ok=1
record F1 "Simulator unavailable (503) ${DUR}s" "$det" \
  "breaker OPEN; cached state served (HTTP $code, ${age}s old); SAFE_HOLD after ${hold}s; no writes" \
  "$rec" "$(verdict "$det" "$rec" "$ok")" "recover counted from fault expiry"
