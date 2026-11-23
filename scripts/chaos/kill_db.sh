#!/usr/bin/env bash
# F9: Postgres down. The engine keeps operating; history writes are buffered and flushed on recovery.
source "$(dirname "$0")/lib.sh"
DUR=${1:-30}
precondition || exit 1
docker stop fsip-timescaledb >/dev/null
det=$(wait_until '.components.database.status == "Down"' 30)
tick0=$(st '.data_tick'); sleep "$DUR"; tick1=$(st '.data_tick')
buf=$(st '.components.database.buffered_writes')
docker start fsip-timescaledb >/dev/null
rec=$(wait_until "$healthy and (.components.database.buffered_writes == 0)" 120)
ok=0; [ "$tick1" -gt "$tick0" ] && ok=1
record F9 "Database stopped ${DUR}s" "$det" \
  "engine kept running (tick $tick0->$tick1); $buf writes buffered, flushed after restart" \
  "$rec" "$(verdict "$det" "$rec" "$ok")" "recover counted from container start"
