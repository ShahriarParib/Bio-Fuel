#!/usr/bin/env bash
# F15: the simulator is reset under us. A new run must be detected and the engine must return to NORMAL.
source "$(dirname "$0")/lib.sh"
precondition || exit 1
run0=$(state '.sim_run_id')
curl -s -X POST "$SIM/admin/reset" >/dev/null
sim_running
t0=$(now)
det=timeout
while [ "$(python3 -c "print(int($(now) - $t0 < 30))")" = "1" ]; do
  if [ "$(state '.sim_run_id')" != "$run0" ]; then det=$(since "$t0"); break; fi
  sleep 0.5
done
rec=$(wait_until "$healthy" 60)
run1=$(state '.sim_run_id')
record F15 "Simulator reset" "$det" "new sim_run #$run0 -> #$run1; live state cleared, history kept" \
  "$rec" "$(verdict "$det" "$rec")"
