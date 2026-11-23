#!/usr/bin/env bash
# F11: the engine is SIGKILLed. On restart it rebuilds state from the simulator; no allocation may be duplicated.
source "$(dirname "$0")/lib.sh"
precondition || exit 1
dups() { curl -s "$SIM/v1/allocations" | jq '[.[].idempotency_key] | length - (unique | length)'; }
d0=$(dups)
docker kill fsip-engine >/dev/null
det=$(wait_until '.components.decision_engine.status == "Down"' 20)
docker start fsip-engine >/dev/null
rec=$(wait_until "$healthy" 120)
sleep 10
d1=$(dups)
ok=0; [ "$d0" = "0" ] && [ "$d1" = "0" ] && ok=1
record F11 "Engine killed (SIGKILL), restarted" "$det" \
  "state rebuilt from simulator + DB; duplicate idempotency keys after restart: $d1" \
  "$rec" "$(verdict "$det" "$rec" "$ok")" "recover counted from container start"
