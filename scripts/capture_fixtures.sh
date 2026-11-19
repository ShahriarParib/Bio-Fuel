#!/usr/bin/env bash
# Capture real simulator responses as JSON fixtures for contract tests.
# WARNING: resets the simulator. Usage: SIM_URL=http://localhost:8000 bash scripts/capture_fixtures.sh
set -euo pipefail
SIM_URL="${SIM_URL:-http://localhost:${SIM_HOST_PORT:-8000}}"
OUT="${OUT:-libs/fsip_common/tests/fixtures}"
mkdir -p "$OUT"
J='Content-Type: application/json'
get()  { curl -sf "$SIM_URL$1" > "$OUT/$2.json"; echo "captured $2"; }
post() { curl -s -o "$OUT/$3.json" -w '%{http_code}' -X POST -H "$J" "$SIM_URL$1" ${2:+-d "$2"}; echo " captured $3"; }

curl -sf -X POST "$SIM_URL/admin/reset" >/dev/null
curl -sf -X POST "$SIM_URL/admin/faults/clear" >/dev/null
get /v1/health health
get /v1/instance instance
get /v1/regions regions
get /v1/depots depots
get /v1/depots/depot-gazipur depot_single
get /v1/stations stations
get /v1/routes routes
get /v1/supply-arrivals supply_arrivals
ALLOC='{"idempotency_key":"fx-1","source_depot_id":"depot-gazipur","destination_station_id":"station-mirpur","route_id":"route-gazipur-mirpur","fuel_type":"DIESEL","quantity":3000}'
post /v1/allocations "$ALLOC" allocation_created_201
post /v1/allocations "$ALLOC" allocation_replay_201
post /v1/allocations '{"idempotency_key":"fx-1","source_depot_id":"depot-gazipur","destination_station_id":"station-mirpur","route_id":"route-gazipur-mirpur","fuel_type":"DIESEL","quantity":10}' error_409_idempotency_mismatch
post /v1/allocations '{"idempotency_key":"fx-2","source_depot_id":"depot-gazipur","destination_station_id":"station-tongi","route_id":"route-gazipur-mirpur","fuel_type":"DIESEL","quantity":10}' error_409_route_mismatch
post /v1/allocations '{"idempotency_key":"fx-3","source_depot_id":"depot-gazipur","destination_station_id":"station-mirpur","route_id":"route-gazipur-mirpur","fuel_type":"KEROSENE","quantity":-1}' error_422_validation
post /v1/allocations '{"idempotency_key":"fx-4","source_depot_id":"depot-x","destination_station_id":"station-mirpur","route_id":"route-gazipur-mirpur","fuel_type":"DIESEL","quantity":10}' error_404_not_found
post /admin/events '{"type":"demand_spike","start_tick":0,"duration_ticks":8,"parameters":{"region_ids":["region-dhaka"],"multiplier":1.8}}' admin_event_created
for i in 1 2 3; do curl -sf -X POST "$SIM_URL/admin/step" > "$OUT/admin_step.json"; done; echo "captured admin_step"
get /v1/events events
get /v1/allocations allocations
get "/v1/demand-history?limit=24" demand_history
get /v1/metrics metrics
get "/admin/audit?limit=20" admin_audit
curl -sf -X POST -H "$J" "$SIM_URL/admin/faults" -d '{"type":"unavailable","duration_seconds":3}' > "$OUT/admin_fault_created.json"
curl -s -o "$OUT/error_503_fault_injected.json" "$SIM_URL/v1/depots"; echo "captured error_503_fault_injected"
curl -sf -X POST "$SIM_URL/admin/faults/clear" >/dev/null
curl -sf -X POST -H "$J" "$SIM_URL/admin/faults" -d '{"type":"stale_data","duration_seconds":3}' >/dev/null
curl -s -D "$OUT/stale_headers.txt" -o /dev/null "$SIM_URL/v1/instance"; echo "captured stale_headers"
curl -sf -X POST "$SIM_URL/admin/faults/clear" >/dev/null
curl -sf -X POST "$SIM_URL/admin/reset" >/dev/null
echo "done -> $OUT"
