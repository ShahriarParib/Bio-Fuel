# 21: API Contracts (exact request/response shapes)

Conventions (from `20` §6):
- JSON, snake_case fields, times as ISO-8601 UTC strings with `Z`, liters as numbers (float), ticks as ints.
- Errors: `{"detail":{"code":"UPPER_SNAKE","message":"...","data":{}}}`. Common codes: `UNAUTHORIZED` 401,
  `FORBIDDEN` 403, `NOT_FOUND` 404, `CONFLICT` 409, `RECOMMENDATION_EXPIRED` 409, `STALE_DATA` 409,
  `CONSTRAINT_VIOLATION` 422 (with `data.violations`), `RATE_LIMITED` 429 (+ `Retry-After`), `DEPENDENCY_DOWN` 503,
  `DEMO_CONTROLS_DISABLED` 403.
- Every api response has headers `X-Data-Tick: <int|-1>` and `X-Degraded: true|false`.
- Auth: read endpoints are public (viewer). Write endpoints need `Authorization: Bearer <JWT>` (phase 19) or, before phase 19,
  `Authorization: Bearer ${API_OPERATOR_TOKEN}`, which counts as role `operator`. The required role is listed per endpoint.
- Pydantic: each request/response below is a pydantic model in `services/api/app/schemas.py` with the same name. TS mirror in
  `frontend/src/api/types.ts`.

---

## 1. Shared object shapes

```jsonc
// FuelMap
{"DIESEL": 9000.0, "PETROL": 9000.0, "OCTANE": 5000.0}

// RiskCell  (per station × fuel)
{"level": "HIGH", "hours_to_stockout": 6.2, "stockout_prob_lead": 0.41, "stockout_prob_horizon": 0.72,
 "inventory": 8400.0, "inflight": 0.0, "fill_pct": 0.56, "cover_hours": 9.1,
 "drivers": [{"signal": "demand_multiplier", "value": 1.8, "detail": "demand_spike event #3 (Dhaka) until t=160"}],
 "forecast_model": "seasonal@builtin"}

// StationView
{"id": "station-mirpur", "name": "Mirpur Fuel Station", "region_id": "region-dhaka", "status": "OPEN",
 "demand_profile": "urban_high", "demand_multiplier": 1.0,
 "capacity": FuelMap, "inventory": FuelMap, "inflight": FuelMap, "fill_pct": FuelMap,
 "risk": {"DIESEL": RiskCell, "PETROL": RiskCell, "OCTANE": RiskCell},
 "routes_available": 2, "single_route": false}

// DepotView
{"id": "depot-gazipur", "name": "Gazipur Depot", "region_id": "region-dhaka", "status": "OPEN",
 "dispatch_capacity_per_tick": 12000.0, "dispatch_used_this_tick": 6500.0,
 "capacity": FuelMap, "inventory": FuelMap,
 "next_supply": [{"id": "supply-101", "fuel_type": "DIESEL", "quantity": 12000.0, "planned_tick": 64, "status": "SCHEDULED"}],
 "cover_hours": FuelMap, "headroom_before_next_supply": FuelMap, "constrained_self_cap": false}

// RouteView
{"id": "route-gazipur-mirpur", "source_depot_id": "depot-gazipur", "destination_station_id": "station-mirpur",
 "transit_ticks": 2, "max_shipment": 7000.0, "status": "AVAILABLE",
 "scheduled_disruption": {"event_id": 4, "start_tick": 160, "end_tick": 184} /* or null */}

// AllocationView (our in-flight/active shipments; progress for the map/graph animation)
{"sim_allocation_id": 12, "decision_id": 7, "line_id": 19, "idempotency_key": "fsip-3-7-1",
 "route_id": "route-gazipur-mirpur", "source_depot_id": "depot-gazipur", "destination_station_id": "station-mirpur",
 "fuel_type": "DIESEL", "quantity": 5000.0, "status": "IN_TRANSIT", "created_tick": 138, "departure_tick": 138,
 "expected_arrival_tick": 140, "progress": 0.5}

// Alert
{"id": 41, "fingerprint": "stockout:station-mirpur:DIESEL", "category": "STOCKOUT_RISK", "severity": "CRITICAL",
 "status": "OPEN", "title": "Mirpur DIESEL projected stockout in 6.2 h", "message": "...",
 "entity_type": "station", "entity_id": "station-mirpur", "fuel_type": "DIESEL",
 "opened_tick": 131, "resolved_tick": null, "opened_at": "2026-11-20T10:15:02Z", "acknowledged_by": null,
 "related_decision_id": 7, "data": {}}

// DecisionLine
{"id": 19, "line_no": 1, "source_depot_id": "depot-gazipur", "destination_station_id": "station-mirpur",
 "route_id": "route-gazipur-mirpur", "fuel_type": "DIESEL", "quantity": 5000.0, "idempotency_key": "fsip-3-7-1",
 "status": "PLANNED", "sim_allocation_id": null, "sim_error_code": null, "expected_arrival_tick": 140,
 "actual_arrival_tick": null, "received_liters": null, "reason": "Mirpur DIESEL CRITICAL: stockout in 6.2 h"}

// Decision (summary form in lists; full form adds explanation + narrative + impact)
{"id": 7, "sim_run_id": 3, "tick": 138, "policy": "optimizer", "policy_version": "optimizer@1.0.0",
 "trigger": "alert", "status": "PENDING_APPROVAL", "mode": "advisory", "confidence": 0.81, "degraded": false,
 "fallback_reason": null, "expires_at_tick": 140, "total_liters": 5000.0, "lines": [DecisionLine],
 "summary": "Ship 5,000 L DIESEL gazipur→mirpur", "requires_second_approval": false,
 "approvals": [{"actor": "op1", "role": "operator", "at": "2026-11-20T10:16:00Z"}],
 "impact": {"targets": [{"station_id": "station-mirpur", "fuel_type": "DIESEL",
             "stockout_prob_before": 0.72, "stockout_prob_after": 0.19,
             "unmet_before": 2300.0, "unmet_after": 150.0}],
            "network": {"unmet_before": 2300.0, "unmet_after": 150.0, "overflow_after": 0.0}},
 "explanation": Explanation /* 05 §7.1 */, "narrative": "...", "narrative_source": "template" /* or "llm" */}

// Incident
{"id": 5, "kind": "DEPENDENCY_DOWN", "component": "prediction_service", "status": "RESOLVED",
 "started_at": "...", "mitigated_at": "...", "resolved_at": "...", "summary": "...",
 "timeline": [{"at": "...", "event": "detected: breaker OPEN after 5 failures"},
              {"at": "...", "event": "mitigated: fallback seasonal@builtin active"},
              {"at": "...", "event": "recovered: half-open probe OK"}]}
```

## 2. api (public, `/api/*`, via the frontend proxy `http://localhost:3000/api`)

### 2.1 State & stream
| Method Path | Role | Query/Body | 200 Response | Notes |
|---|---|---|---|---|
| GET `/api/state` | viewer | `If-None-Match` | `WorldState` (below) | ETag = `"<sim_run_id>-<tick>"`; 304 if unchanged; falls back to the DB snapshot with `degraded:true` |
| GET `/api/stream` | viewer | header `Last-Event-ID` | `text/event-stream` | see §2.9 |
| GET `/api/stations/{id}` | viewer | `history_ticks=96` | `{station: StationView, history: [{tick, inventory: FuelMap}], demand: [{tick, fuel_type, demand, served, unmet}], forecast: ForecastSeries[]}` | 404 NOT_FOUND |
| GET `/api/depots/{id}` | viewer | `history_ticks=96` | `{depot: DepotView, history: [...], projection: [{tick, inventory: FuelMap}]}` | |
| GET `/api/cover` | viewer | | `{network: {DIESEL: {available, demand_96, cover_days}}, regions: {...}, supply_ends_tick: 212, scarcity: {PETROL: false}}` | F-COVER |

`WorldState`:
```jsonc
{"sim_run_id": 3, "tick": 140, "sim_time": "2026-01-02T11:00:00Z", "tick_minutes": 15, "sim_status": "RUNNING",
 "clock_label": "Day 2 11:00 · t=140", "generated_at": "2026-11-20T10:16:03Z", "age_seconds": 0.4,
 "stale": false, "degraded": false, "degraded_reasons": [], "engine_mode": "NORMAL", "operating_mode": "advisory",
 "policy": "optimizer", "scarcity": {"DIESEL": false, "PETROL": false, "OCTANE": false},
 "metrics": {"service_level": 0.998, "served_liters": 123000.0, "unmet_liters": 240.0, "allocation_liters": 98000.0,
             "allocation_failures": 0, "overflow_liters_est": 0.0, "depot_clipped_liters_est": 0.0},
 "depots": [DepotView], "stations": [StationView], "routes": [RouteView],
 "events": [{"id", "type", "start_tick", "end_tick", "status", "parameters"}],
 "supply_upcoming": [{"id", "depot_id", "fuel_type", "quantity", "planned_tick", "status"}],
 "allocations_active": [AllocationView],
 "alerts_open_count": {"INFO": 0, "WARNING": 2, "CRITICAL": 1},
 "pending_recommendations": [{"decision_id": 7, "summary": "...", "confidence": 0.81, "total_liters": 5000.0,
                              "worst_level": "CRITICAL", "expires_at_tick": 142}],
 "bottleneck": {"type": "DISPATCH_CAPACITY", "label": "depot-patiya dispatch 11,000/11,000"} /* or null, P18 */,
 "system": {"simulator": "UP", "sse": "CONNECTED", "intelligence": "UP", "db": "UP", "redis": "UP",
            "breakers": {"simulator": "CLOSED", "intelligence": "CLOSED"}, "forecaster": "seasonal@builtin"}}
```

### 2.2 Risk, forecast, alerts
| Method Path | Role | Params | Response |
|---|---|---|---|
| GET `/api/risk` | viewer | `region_id?`, `fuel?`, `min_level?` | `{"tick": 140, "rows": [{"station_id", "fuel_type", ...RiskCell}]}` sorted CRITICAL→LOW, then hours asc |
| GET `/api/forecast` | viewer | `station_id`, `fuel`, `horizon=48`, `history=96` | `ForecastSeries` = `{"station_id", "fuel_type", "model": "seasonal@builtin", "is_fallback": false, "history": [{"tick", "demand"}], "points": [{"tick", "p10", "p50", "p90"}], "coverage_80": 0.79}` |
| GET `/api/alerts` | viewer | `status=OPEN`, `category?`, `severity?`, `limit=100` | `{"items": [Alert], "total": 12}` |
| POST `/api/alerts/{id}/ack` | operator | `{}` | `Alert` (status ACKNOWLEDGED) |
| GET `/api/incidents` | viewer | `status?`, `limit=50` | `{"items": [Incident]}` |

### 2.3 Recommendations & decisions
| Method Path | Role | Body | Response / errors |
|---|---|---|---|
| GET `/api/recommendations` | viewer | | `{"items": [Decision (full)]}`, statuses PENDING_APPROVAL + AWAITING_SECOND_APPROVAL |
| GET `/api/decisions` | viewer | `status?`, `policy?`, `from_tick?`, `limit=50`, `offset=0` | `{"items": [Decision (summary)], "total": n}` |
| GET `/api/decisions/{id}` | viewer | | `Decision (full)` + `"outcome": {...} or null` |
| POST `/api/decisions/{id}/approve` | operator | `{"line_ids": [19] /* optional subset */, "quantities": {"19": 4500} /* optional modify */, "reviewed_driver": true}` | 200 `Decision`. 409 `RECOMMENDATION_EXPIRED`, 409 `STALE_DATA`, 422 `CONSTRAINT_VIOLATION` (`data.violations: [{"line_id", "rule", "message"}]`), 422 `REVIEW_REQUIRED` (cognitive forcing, F-TRUST), 403 `FORBIDDEN` (four-eyes: same user twice / role too low) |
| POST `/api/decisions/{id}/reject` | operator | `{"reason_code": "WRONG_PRIORITY\|DATA_LOOKS_WRONG\|PREFER_OTHER_ROUTE\|NOT_NEEDED\|OTHER", "comment": "..."}` | `Decision` (REJECTED) |
| POST `/api/plan-now` | operator | `{}` | 202 `{"accepted": true, "will_run_at_tick": 141}` |
| POST `/api/manual-allocation` | operator | `{"source_depot_id", "destination_station_id", "route_id", "fuel_type", "quantity", "comment"}` | 201 `Decision` (policy=manual, executed) or 422 CONSTRAINT_VIOLATION |
| GET `/api/reports/reconciliation` | viewer | `from_tick?`, `to_tick?` | `{"lines": [{"line_id", "sent", "received", "loss", "loss_reason", "method"}], "totals": {"sent", "received", "loss", "loss_rate"}, "stations": [{"station_id", "fuel_type", "variance_cusum", "alarm"}]}` |

### 2.4 What-if & lab
| Method Path | Role | Body | Response |
|---|---|---|---|
| POST `/api/whatif` | viewer (rate-limited 10/min) | `{"horizon_ticks": 96, "lines": [{"route_id", "fuel_type", "quantity"}] /* ≤20 */, "events": [{"type", "start_offset", "duration_ticks", "parameters"}] /* ≤5 */, "compare": ["do_nothing", "recommended", "custom"]}` | `{"tick": 140, "scenarios": [{"name": "do_nothing", "unmet": 5200.0, "overflow": 0.0, "min_station_sl": 0.81, "stations": [{"station_id", "fuel_type", "stockout_prob", "first_stockout_tick", "inventory_p50": [..]}]}], "disclaimer": "Projection by our model; not executed."}` |
| POST `/api/lab/compare-policies` | viewer | `{"horizon_ticks": 96}` | `{"policies": [{"name": "history_based", "unmet", "min_station_sl", "jain"}]}` (P17) |
| GET `/api/resilience/n1` | viewer | | `{"computed_at_tick": 128, "rows": [{"asset_type": "route", "asset_id", "duration_ticks": 48, "unmet", "first_stockout_tick", "tts_hours", "ttr_hours", "critical": true}]}` (P18) |

### 2.5 System, metrics, models
| Method Path | Role | Response |
|---|---|---|
| GET `/api/system/status` | viewer | `{"overall": "Healthy\|Degraded\|Down", "engine_mode", "components": {"backend_api"\|"database"\|"redis"\|"fuel_simulator"\|"prediction_service"\|"decision_engine"\|"notifier": {"status", "latency_ms?", "breaker?", "p95_ms?", "error_rate?", "stale?", "sse?", "fallback?", "policy?", "last_cycle_ms?", "ticks_behind?", "error?"}}, "versions": {"api", "engine", "intelligence", "frontend", "git_sha"}, "p95_latency_ms", "error_rate", "slo": [{"name", "target", "current", "budget_remaining"}]}` |
| GET `/api/metrics/summary` | viewer | `{"service_level", "unmet_24h", "stations_at_risk", "open_alerts", "pending_recommendations", "allocation_failures", "overflow_liters", "fairness_jain", "min_station_sl", "acceptance_rate", "override_rate"}` |
| GET `/api/models` | viewer | `{"active": {"name", "version", "metrics": {"wape", "coverage_80"}}, "available": [...], "live": {"wape_rolling", "coverage_rolling", "drift": false}}` |
| POST `/api/models/activate` | supervisor | body `{"name", "version"}` → `{"active": {...}}` (ledgered) |
| POST `/api/control/mode` | operator | body `{"mode": "advisory\|autopilot"}` → `{"mode"}`; 409 `STALE_DATA` if turning autopilot on while degraded |
| POST `/api/control/policy` | supervisor | body `{"policy": "optimizer\|heuristic", "scarcity_policy?"}` |
| POST `/api/control/reserve/release` | supervisor | body `{"depot_id", "fuel_type", "liters", "reason"}` (P17) |
| GET `/api/audit` | viewer | `{"items": [{"id", "at", "actor", "action", "entity_type", "entity_id", "payload"}]}` |
| GET `/api/ledger` / `/api/ledger/verify` / `/api/ledger/export.jsonl` | viewer | P19, see 12 F-LEDGER |
| GET `/api/replay/{sim_run_id}` | viewer | `from_tick`, `to_tick` (≤ 500) → `{"frames": [WorldState-lite], "markers": [{"tick", "type", "id", "label"}]}` (P17) |
| GET `/api/breakglass/plan.csv` | viewer | CSV: `depot,station,route,fuel,quantity,reason,generated_tick` |

### 2.6 Assistant
`POST /api/assistant/ask` (viewer, rate-limited 20/min) body `{"question": "Why is Cox's Bazar at risk?"}` →
`{"answer": "...", "engine": "llm|rules", "sources": [{"type": "alert|decision|station|event", "id": "41"}], "generated_at": "..."}`.
`GET /api/summary?window_ticks=96` → `{"text": "...", "engine": "llm|template", "stats": {...}}`.

### 2.7 Auth (P19)
`POST /api/auth/login` `{"username", "password"}` → `{"access_token", "token_type": "bearer", "role", "expires_in": 28800}`;
401 `UNAUTHORIZED`. `GET /api/auth/me` → `{"username", "role"}`.

### 2.8 Demo controls (only if `DEMO_CONTROLS=true`, otherwise 403 `DEMO_CONTROLS_DISABLED`; role operator; all audited)
| Path | Body | Calls simulator |
|---|---|---|
| POST `/api/control/sim/run` \| `pause` \| `step` \| `reset` | step: `{"count": 1..50}` | `/admin/run`, `/admin/pause`, `/admin/step` ×n, `/admin/reset` |
| POST `/api/control/scenario/{preset}` | `{}` | `/admin/events` with `start_tick = current tick`. Presets: `demand_spike_dhaka` (×1.8, 24 ticks), `route_cut_mirpur` (route-gazipur-mirpur, 24), `depot_constraint_patiya` (24), `shipment_delay_gazipur` (+8), `supply_shortfall` (0.5), `station_outage_tongi` (12), `single_route_cut` (tongi+coxsbazar routes, 40), `combined_crisis` (spike + route cut + delay) |
| POST `/api/control/chaos/{fault}` | `{"duration_seconds": 60, "parameters": {}}` | `/admin/faults` (`unavailable`, `error_rate`, `latency`, `stale_data`, `stream_disconnect`) |
| POST `/api/control/chaos/clear` | `{}` | `/admin/faults/clear` |

### 2.9 SSE `/api/stream`
- Each message: `id: <redis stream id>` + `event: <type>` + `data: <json>`; heartbeat comment `: hb` every 15 s.
- On connect with `Last-Event-ID`, replay from that id (max 1,000 events), then live.
- Types and payloads:
| event | data |
|---|---|
| `state.updated` | `{"tick": 141, "etag": "3-141"}` → the client refetches `/api/state` (If-None-Match) |
| `alert.opened` / `alert.updated` / `alert.resolved` | `Alert` |
| `decision.created` / `.approved` / `.rejected` / `.executed` / `.expired` / `.outcome` | `Decision (summary)` |
| `incident.opened` / `.updated` / `.resolved` | `Incident` |
| `mode.changed` | `{"engine_mode", "operating_mode", "reasons": []}` |
| `scarcity.entered` / `.exited` | `{"fuel_type", "cover_ratio"}` |
| `playbook.started` / `.step` / `.completed` | `{"run_id", "playbook_id", "step_id?", "status"}` |
| `reset.detected` | `{"old_run_id", "new_run_id"}` |

## 3. engine internal (`http://engine:8100`, header `X-Internal-Token`)
| Method Path | Body | Response |
|---|---|---|
| GET `/health/live` | | `{"status": "ok", "service": "engine"}` |
| GET `/health/ready` | | 200 `{"status": "ready\|degraded", "last_sync_age_s", "engine_mode"}`; 503 only if never synced |
| GET `/metrics/` | | Prometheus text |
| GET `/debug/state` | | `WorldState` (same as `fsip:state`) |
| POST `/internal/decisions/{id}/approve` | `{"actor", "role", "line_ids?", "quantities?", "reviewed_driver"}` | `Decision` or error codes as in 2.3 |
| POST `/internal/decisions/{id}/reject` | `{"actor", "reason_code", "comment"}` | `Decision` |
| POST `/internal/plan-now` | `{"actor"}` | 202 |
| POST `/internal/mode` | `{"actor", "mode"}` | `{"mode"}` |
| POST `/internal/policy` | `{"actor", "policy", "scarcity_policy?"}` | `{"policy", "scarcity_policy"}` |
| POST `/internal/manual-allocation` | `{"actor", ...line}` | `Decision` |
| POST `/internal/whatif` | same as `/api/whatif` | same |
| POST `/internal/reserve/release` | `{"actor", "depot_id", "fuel_type", "liters", "reason"}` | `{"reserve": FuelMap}` |
The api forwards `actor` and `role` from the JWT; the engine trusts them because of the internal token.

## 4. intelligence (`http://intelligence:8200`)
| Method Path | Body | Response |
|---|---|---|
| POST `/forecast` | `{"tick": 140, "tick_minutes": 15, "horizon": 48, "series": [{"station_id", "fuel_type", "history": [{"tick", "demand", "multiplier"}] /* ≤ 192, oldest→newest */, "multiplier_now": 1.0, "multiplier_schedule": [{"from_tick", "to_tick", "multiplier"}]}]}` | `{"model": "lightgbm", "version": "1.0.0", "series": [{"station_id", "fuel_type", "points": [{"tick", "p10", "p50", "p90"}]}]}`. Output validation: finite, ≥ 0, p10 ≤ p50 ≤ p90 (else 500 `BAD_MODEL_OUTPUT`, and the engine falls back) |
| POST `/anomaly/score` | `{"points": [{"station_id", "fuel_type", "tick", "actual", "p10", "p50", "p90"}]}` | `{"scores": [{"station_id", "fuel_type", "tick", "z", "is_anomaly"}]}` |
| GET `/models` | | `{"active": {"name", "version", "metrics"}, "available": [{"name", "version", "metrics", "created_at"}]}` |
| POST `/models/activate` | `{"name", "version"}` | `{"active": {...}}`; 404 if unknown |
| POST `/lab/n1` | `{"state": WorldState \| null /* null = latest from Redis */, "durations": [24, 48], "horizon": 96}` | as `/api/resilience/n1` |
| POST `/lab/compare-policies` | `{"state": null, "horizon": 96}` | as `/api/lab/compare-policies` |
| GET `/health/live`, `/health/ready` (`{"status", "model"}`), `/metrics/` | | |

## 5. TypeScript mirror (put in `frontend/src/api/types.ts`; extend as phases add endpoints)
```ts
export type Fuel = "DIESEL" | "PETROL" | "OCTANE";
export type FuelMap = Record<Fuel, number>;
export type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type Severity = "INFO" | "WARNING" | "CRITICAL";
export type HealthStatus = "Healthy" | "Degraded" | "Down";
export type EngineMode = "NORMAL" | "DEGRADED" | "SAFE_HOLD" | "NO_FEASIBLE_ROUTES";
export type DecisionStatus = "PROPOSED" | "PENDING_APPROVAL" | "AWAITING_SECOND_APPROVAL" | "APPROVED" | "REJECTED"
  | "EXECUTED" | "PARTIAL" | "EXPIRED" | "FAILED";
export type LineStatus = "PLANNED" | "SUBMITTED" | "DEFERRED" | "PENDING" | "IN_TRANSIT" | "ARRIVED" | "FAILED"
  | "CANCELLED" | "REJECTED_BY_SIM";
export interface Driver { signal: string; value: string | number; detail: string }
export interface RiskCell { level: RiskLevel; hours_to_stockout: number | null; stockout_prob_lead: number;
  stockout_prob_horizon: number; inventory: number; inflight: number; fill_pct: number; cover_hours: number | null;
  drivers: Driver[]; forecast_model: string }
export interface StationView { id: string; name: string; region_id: string; status: "OPEN" | "OUTAGE";
  demand_profile: string; demand_multiplier: number; capacity: FuelMap; inventory: FuelMap; inflight: FuelMap;
  fill_pct: FuelMap; risk: Record<Fuel, RiskCell>; routes_available: number; single_route: boolean }
export interface DepotView { id: string; name: string; region_id: string; status: "OPEN" | "CONSTRAINED";
  dispatch_capacity_per_tick: number; dispatch_used_this_tick: number; capacity: FuelMap; inventory: FuelMap;
  next_supply: { id: string; fuel_type: Fuel; quantity: number; planned_tick: number; status: string }[];
  cover_hours: Record<Fuel, number | null>; headroom_before_next_supply: FuelMap; constrained_self_cap: boolean }
export interface RouteView { id: string; source_depot_id: string; destination_station_id: string; transit_ticks: number;
  max_shipment: number; status: "AVAILABLE" | "DISRUPTED";
  scheduled_disruption: { event_id: number; start_tick: number; end_tick: number } | null }
export interface AllocationView { sim_allocation_id: number; decision_id: number | null; line_id: number | null;
  idempotency_key: string; route_id: string; source_depot_id: string; destination_station_id: string; fuel_type: Fuel;
  quantity: number; status: LineStatus; created_tick: number; departure_tick: number | null;
  expected_arrival_tick: number | null; progress: number }
export interface Alert { id: number; fingerprint: string; category: string; severity: Severity;
  status: "OPEN" | "ACKNOWLEDGED" | "RESOLVED"; title: string; message: string; entity_type: string | null;
  entity_id: string | null; fuel_type: Fuel | null; opened_tick: number; resolved_tick: number | null;
  opened_at: string; acknowledged_by: string | null; related_decision_id: number | null; data: Record<string, unknown> }
export interface DecisionLine { id: number; line_no: number; source_depot_id: string; destination_station_id: string;
  route_id: string; fuel_type: Fuel; quantity: number; idempotency_key: string; status: LineStatus;
  sim_allocation_id: number | null; sim_error_code: string | null; expected_arrival_tick: number | null;
  actual_arrival_tick: number | null; received_liters: number | null; reason: string }
export interface Impact { targets: { station_id: string; fuel_type: Fuel; stockout_prob_before: number;
  stockout_prob_after: number; unmet_before: number; unmet_after: number }[];
  network: { unmet_before: number; unmet_after: number; overflow_after: number } }
export interface Explanation { headline: string; why_at_risk: Driver[]; constraints: string[];
  expected_impact: Record<string, number>; confidence: number; confidence_factors: Record<string, number>;
  alternatives: { name: string; stockout_prob_after?: number; unmet_after?: number; why_not?: string }[];
  policy: string; forecast_model: string; degraded: boolean }
export interface Decision { id: number; sim_run_id: number; tick: number; policy: "optimizer" | "heuristic" | "manual";
  policy_version: string; trigger: string; status: DecisionStatus; mode: "advisory" | "autopilot"; confidence: number;
  degraded: boolean; fallback_reason: string | null; expires_at_tick: number; total_liters: number; lines: DecisionLine[];
  summary: string; requires_second_approval: boolean; approvals: { actor: string; role: string; at: string }[];
  impact?: Impact; explanation?: Explanation; narrative?: string; narrative_source?: "template" | "llm" }
export interface WorldState { sim_run_id: number; tick: number; sim_time: string; tick_minutes: number;
  sim_status: "PAUSED" | "RUNNING"; clock_label: string; generated_at: string; age_seconds: number; stale: boolean;
  degraded: boolean; degraded_reasons: string[]; engine_mode: EngineMode; operating_mode: "advisory" | "autopilot";
  policy: string; scarcity: Record<Fuel, boolean>;
  metrics: { service_level: number; served_liters: number; unmet_liters: number; allocation_liters: number;
    allocation_failures: number; overflow_liters_est: number; depot_clipped_liters_est: number };
  depots: DepotView[]; stations: StationView[]; routes: RouteView[];
  events: { id: number; type: string; start_tick: number; end_tick: number; status: string; parameters: Record<string, unknown> }[];
  supply_upcoming: { id: string; depot_id: string; fuel_type: Fuel; quantity: number; planned_tick: number; status: string }[];
  allocations_active: AllocationView[]; alerts_open_count: Record<Severity, number>;
  pending_recommendations: { decision_id: number; summary: string; confidence: number; total_liters: number;
    worst_level: RiskLevel; expires_at_tick: number }[];
  bottleneck: { type: string; label: string } | null;
  system: Record<string, unknown> }
```
