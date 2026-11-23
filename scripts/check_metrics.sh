#!/usr/bin/env bash
# Fails if any metric the dashboards, alerts and SLOs depend on is missing from a service's /metrics.
set -u
ENGINE=${ENGINE:-http://localhost:8100}
API=${API_URL:-http://localhost:18080}
INTEL=${INTEL:-http://localhost:8200}
PROM=${PROM:-http://localhost:9090}

declare -A REQUIRED=(
  [engine]="fsip_service_level fsip_sim_served_liters fsip_sim_unmet_liters fsip_stale_data fsip_engine_mode
            fsip_component_status fsip_breaker_state fsip_ticks_behind fsip_sse_connected fsip_sse_reconnects_total
            fsip_cycle_duration_seconds fsip_sim_request_duration_seconds fsip_sim_errors_total
            fsip_sim_invalid_responses_total fsip_db_buffered_writes fsip_db_dropped_writes_total
            fsip_station_inventory_liters fsip_depot_inventory_liters fsip_inflight_liters fsip_stockout_prob
            fsip_network_cover_days fsip_decisions_total fsip_decision_plan_seconds fsip_allocations_submitted_total
            fsip_allocation_failures_total fsip_pending_recommendations fsip_alerts_open fsip_alerts_opened_total
            fsip_fallback_activations_total fsip_forecast_wape fsip_forecast_noise_floor_wape
            fsip_forecast_interval_coverage fsip_forecast_remote fsip_forecast_request_seconds fsip_incidents_total
            fsip_unmet_prevented_liters fsip_counterfactual_unmet_liters fsip_slo_budget_remaining fsip_engine_info"
  [api]="http_requests_total http_request_duration_seconds"
  [intelligence]="fsip_forecast_requests_total fsip_forecast_compute_seconds fsip_model_info"
)
declare -A URL=([engine]=$ENGINE [api]=$API [intelligence]=$INTEL)

fail=0
for svc in engine api intelligence; do
  body=$(curl -s --max-time 5 "${URL[$svc]}/metrics/")
  if [ -z "$body" ]; then echo "FAIL $svc: /metrics unreachable"; fail=1; continue; fi
  missing=()
  for m in ${REQUIRED[$svc]}; do
    grep -qE "^(# TYPE )?$m(\{|_bucket|_total| |$)" <<<"$body" || missing+=("$m")
  done
  if [ ${#missing[@]} -eq 0 ]; then
    echo "OK   $svc: $(wc -w <<<"${REQUIRED[$svc]}") metrics present"
  else
    echo "FAIL $svc: missing ${missing[*]}"; fail=1
  fi
done

# when Prometheus is up: every target scraped and the SLO recording rules evaluating
if curl -s --max-time 3 "$PROM/-/ready" >/dev/null 2>&1; then
  down=$(curl -s "$PROM/api/v1/targets" | jq -r '.data.activeTargets[] | select(.health != "up") | .labels.job' | sort -u | tr '\n' ' ')
  [ -z "$down" ] && echo "OK   prometheus: all targets up" || { echo "FAIL prometheus: targets down: $down"; fail=1; }
  n=$(curl -s "$PROM/api/v1/query" --data-urlencode 'query=count(slo:error_budget_remaining:ratio)' | jq -r '.data.result[0].value[1] // 0')
  [ "$n" -ge 4 ] 2>/dev/null && echo "OK   prometheus: $n SLO budgets recorded" || { echo "FAIL prometheus: SLO rules not evaluating ($n)"; fail=1; }
  bad=$(curl -s "$PROM/api/v1/rules" | jq -r '.data.groups[].rules[] | select(.health != "ok") | .name' | tr '\n' ' ')
  [ -z "$bad" ] && echo "OK   prometheus: all rules healthy" || { echo "FAIL prometheus: unhealthy rules: $bad"; fail=1; }
fi
[ $fail -eq 0 ] && echo "METRICS OK" || echo "METRICS FAILED"
exit $fail
