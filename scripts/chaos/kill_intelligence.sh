#!/usr/bin/env bash
# F7: the prediction service is stopped. Decisions must continue on the built-in fallback forecaster.
source "$(dirname "$0")/lib.sh"
DUR=${1:-40}
precondition || exit 1
docker stop fsip-intelligence >/dev/null
det=$(wait_until '.components.prediction_service.status != "Healthy"' 30)
sleep "$DUR"
src=$(state '.forecast.source')
docker start fsip-intelligence >/dev/null
rec=$(wait_until "$healthy" 90)
ok=0; [ "$src" = "fallback" ] && ok=1
record F7 "Prediction service stopped ${DUR}s" "$det" \
  "forecast source=$src (seasonal fallback); confidence 0.6 so a human reviews" \
  "$rec" "$(verdict "$det" "$rec" "$ok")" "recover counted from container start"
