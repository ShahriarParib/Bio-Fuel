#!/usr/bin/env bash
# F5: the simulator refuses SSE connections. Finding: the fault only blocks NEW connections; an open stream keeps
# flowing. So the drill forces a reconnect during the fault (engine restart) and checks the engine runs on polling.
source "$(dirname "$0")/lib.sh"
DUR=${1:-60}
precondition || exit 1
fault stream_disconnect "$DUR"
t0=$(now)
docker restart fsip-engine >/dev/null
det=$(wait_until '.components.live_stream.status == "Degraded"' 40)
tick0=$(st '.data_tick'); sleep 8; tick1=$(st '.data_tick')
wait_fault_end "$t0" "$DUR"
rec=$(wait_until "$healthy" 90)
ok=0; [ "$tick1" -gt "$tick0" ] && ok=1
record F5 "SSE refused ${DUR}s (+ forced reconnect)" "$det" \
  "polling fallback kept ticks moving $tick0->$tick1; reconnect with backoff" \
  "$rec" "$(verdict "$det" "$rec" "$ok")" "detect includes the engine boot; recover counted from fault expiry"
