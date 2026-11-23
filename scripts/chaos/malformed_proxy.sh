#!/usr/bin/env bash
# F6: malformed payloads. The engine reads the simulator through the chaos proxy, which corrupts /v1/stations.
source "$(dirname "$0")/lib.sh"
MODE=${1:-wrong_type}; DUR=${2:-30}
quarantined() { docker exec fsip-timescaledb psql -U fsip -d fsip -tAc "select count(*) from quarantine" 2>/dev/null || echo 0; }
make -s chaos-up >/dev/null 2>&1
if ! precondition; then make -s chaos-down >/dev/null 2>&1; exit 1; fi
q0=$(quarantined)
curl -s -X POST "$PROXY/chaos/malform" -H 'content-type: application/json' \
  -d "{\"resource\":\"stations\",\"mode\":\"$MODE\",\"rate\":1.0}" >/dev/null
det=$(wait_until '.components.data_quality.status == "Degraded"' 30)
field=$(st '.components.data_quality.detail')
sleep "$DUR"
q1=$(quarantined)
curl -s -X POST "$PROXY/chaos/clear" >/dev/null
rec=$(wait_until "$healthy" 60)
make -s chaos-down >/dev/null 2>&1
ok=0; [ "$q1" -gt "$q0" ] && ok=1
record F6 "Malformed /v1/stations ($MODE)" "$det" \
  "payload rejected, last-good kept, quarantine rows $q0->$q1; shown: $field" \
  "$rec" "$(verdict "$det" "$rec" "$ok")" "recover counted from clearing the corruption"
