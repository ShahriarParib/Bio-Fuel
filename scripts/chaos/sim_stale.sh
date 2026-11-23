#!/usr/bin/env bash
# F4: the simulator flags its data stale (x-simulator-stale). Autopilot must pause and approvals are blocked.
source "$(dirname "$0")/lib.sh"
DUR=${1:-45}
precondition || exit 1
fault stale_data "$DUR"
t0=$(now)
det=$(wait_until '.components.fuel_simulator.detail | test("STALE")' 20)
stale=$(state '.stale')
mode=$(st '.engine_mode')
wait_fault_end "$t0" "$DUR"
rec=$(wait_until "$healthy" 60)
ok=0; [ "$stale" = "true" ] && [ "$mode" != "NORMAL" ] && ok=1
record F4 "Simulator stale_data for ${DUR}s" "$det" \
  "state.stale=$stale, engine $mode: autopilot paused, approvals 409 STALE_DATA" \
  "$rec" "$(verdict "$det" "$rec" "$ok")" "recover counted from fault expiry"
