#!/usr/bin/env bash
# F10: Redis down. The api must still serve live state (read from the engine directly).
source "$(dirname "$0")/lib.sh"
DUR=${1:-30}
precondition || exit 1
docker stop fsip-redis >/dev/null
det=$(wait_until '.components.redis.status == "Down"' 30)
sleep 3
code=$(curl -s -o /dev/null -w '%{http_code}' "$API/state")
tick0=$(state '.tick'); sleep "$DUR"; tick1=$(state '.tick')
docker start fsip-redis >/dev/null
rec=$(wait_until "$healthy" 90)
ok=0; [ "$code" = "200" ] && [ "$tick1" -gt "$tick0" ] && ok=1
record F10 "Redis stopped ${DUR}s" "$det" \
  "api served live state from the engine (HTTP $code, tick $tick0->$tick1)" \
  "$rec" "$(verdict "$det" "$rec" "$ok")" "recover counted from container start"
