# 02: Simulator Facts (Verified Against the Real Image)

Image: `asifmahmoud414/bup-fuel-supply-simulator:1.0.0` (FastAPI + SQLite, port 8000).
Everything marked **[VERIFIED]** was tested on 2026-11-19 by running the image. **[PDF]** = from the guide, not
independently re-tested. **[UNVERIFIED]** = verify during phase 01 and record the result in `PROGRESS.md`.

---

## 1. Running it

```yaml
services:
  simulator:
    image: asifmahmoud414/bup-fuel-supply-simulator:1.0.0
    environment:
      SIMULATION_SPEED: ${SIMULATION_SPEED:-0.5}   # ticks per wall-second while RUNNING
      TICK_MINUTES: ${TICK_MINUTES:-15}            # simulated minutes per tick
      SIMULATOR_START_MODE: ${SIMULATOR_START_MODE:-paused}
    ports: ["8000:8000"]
```

- **[VERIFIED] Real defaults in the image:** `simulation_speed=8.0`, `tick_minutes=15`, `simulator_start_mode=paused`.
  The PDF compose snippet says `SIMULATION_SPEED:-15` / `TICK_MINUTES:-8`, which contradicts the PDF's own table.
  **Always set both env vars explicitly.**
- Sleep between ticks = `1/SIMULATION_SPEED`, clamped to [0.05 s, 10 s].
- At speed 8: 1 tick = 125 ms; **1 simulated day (96 ticks) = 12 s wall**. At speed 0.5: 1 day ≈ 3.2 min.
- Health: `GET /v1/health` → `{"status":"ok","database":"ok","simulation":{"status":"PAUSED","tick":0}}` (bypasses faults).
- Swagger: `/docs`, OpenAPI: `/openapi.json`, admin HTML console: `/admin`.
- The image has no healthcheck. Write one in compose using python:
  `["CMD","python","-c","import urllib.request;urllib.request.urlopen('http://localhost:8000/v1/health')"]`.
- **[VERIFIED]** `/v1/demand-history` rows are 12 per tick (4 stations × 3 fuels).

### Scenarios baked into the image [VERIFIED]
`/app/scenarios/` contains `baseline.yaml` (active, no preloaded events), `demand_spike.yaml`,
`supply_disruption.yaml`, `final_combined.yaml` (seed 9001). Only `baseline.yaml` is loaded, and the scenario cannot be
switched at runtime. Judges **may** ship a different image or inject the same kinds of events. The other files tell
us what organizers think a crisis looks like:
- `demand_spike`: demand_spike Dhaka ×1.8 at tick 8 for 12 ticks
- `supply_disruption`: shipment_delay Gazipur DIESEL +8 ticks at tick 6, supply_shortfall at tick 10, …
- `final_combined`: demand_spike Dhaka ×1.7 at tick 8 for 16 ticks + shipment_delay at tick 10 + more
→ Our evaluation harness (phase 12) **re-creates these** via `POST /admin/events`. Do not hard-code them in the engine.

---

## 2. Endpoints (all JSON)

### Public read (subject to fault injection, except /v1/health)
| Method | Path | Notes |
|---|---|---|
| GET | `/v1/health` | liveness, bypasses faults |
| GET | `/v1/instance` | `{id, scenario_id, scenario_version, seed, sim_time, tick, tick_minutes, status}` |
| GET | `/v1/regions` | `[{id,name,demand_factor}]` |
| GET | `/v1/depots`, `/v1/depots/{id}` | `{id,name,region_id,status,dispatch_capacity_per_tick,capacity{F},inventory{F}}`, 404 `{"detail":{"code":"NOT_FOUND"}}` |
| GET | `/v1/stations`, `/v1/stations/{id}` | `{id,name,region_id,status,demand_profile,demand_multiplier,capacity{F},inventory{F}}` |
| GET | `/v1/routes` | `{id,source_depot_id,destination_station_id,transit_ticks,max_shipment,status}` |
| GET | `/v1/supply-arrivals` | sorted by planned_tick asc: `{id,depot_id,fuel_type,quantity,planned_tick,actual_tick,status}` |
| GET | `/v1/events` | id desc: `{id,type,start_tick,end_tick,status,parameters}` |
| GET | `/v1/allocations` | id desc: full allocation objects |
| GET | `/v1/demand-history?station_id=&limit=` | limit clamped [1,2000], default 200, newest first |
| GET | `/v1/metrics` | `{served_demand_liters, unmet_demand_liters, service_level, allocation_liters, allocation_failures}` |
| GET | `/v1/stream` | SSE |

### Public write
| Method | Path | Notes |
|---|---|---|
| POST | `/v1/allocations` | the only domain write |
| POST | `/v1/allocations/{id}/cancel` | only when PENDING; refunds depot inventory |

### Admin (bypass faults; testing/demo only)
`POST /admin/run`, `/admin/pause`, `/admin/toggle`, `/admin/step` (→ `{"tick":N,"sim_time":"…"}`), `/admin/reset`,
`GET|POST /admin/events`, `GET|POST /admin/faults`, `POST /admin/faults/clear`, `GET /admin/audit?limit=` (≤1000),
`GET /admin/stats` (row counts, db path, scenario file), `GET /admin` (HTML).

### Enums
- instance.status: `PAUSED | RUNNING`
- depot.status: `OPEN | CONSTRAINED`
- station.status: `OPEN | OUTAGE`
- route.status: `AVAILABLE | DISRUPTED`
- supply.status: `SCHEDULED | DELAYED | ARRIVED`
- event.status: `SCHEDULED | ACTIVE | RESOLVED`
- allocation.status: `PENDING | IN_TRANSIT | ARRIVED | FAILED | CANCELLED`
- fuel: `DIESEL | PETROL | OCTANE`
- event types: `demand_spike | route_disruption | station_outage | depot_constraint | shipment_delay | supply_shortfall`
- fault types: `latency | unavailable | error_rate | stale_data | stream_disconnect`

### Format quirks [VERIFIED]
- `sim_time` comes **without a timezone suffix** (`"2026-01-01T00:15:00"`), while the PDF shows `+00:00`. Parse both;
  treat as UTC.
- Inventory numbers are sometimes ints and sometimes floats (`60000` vs `57000.0`). Parse as float.
- Error shapes differ:
  - domain errors: `{"detail":{"code":"UPPER_SNAKE","message":"…"}}`
  - injected faults: `{"error":{"code":"FAULT_INJECTED","message":"…"}}`
  - stream_disconnect: `{"detail":{"code":"FAULT_INJECTED"}}` (503)
  - validation 422: `{"detail":[{type,loc,msg,input,ctx}, …]}`
- Stale fault: header `x-simulator-stale: true` on non-stream /v1 GETs (header name is case-insensitive).

---

## 3. Allocation mechanics [VERIFIED unless noted]

Request:
```json
{"idempotency_key":"…","source_depot_id":"depot-gazipur","destination_station_id":"station-mirpur",
 "route_id":"route-gazipur-mirpur","fuel_type":"DIESEL","quantity":3000}
```
Validation order (first failure wins) [PDF]: idempotency → NOT_FOUND(404) → ROUTE_MISMATCH → DEPOT_CLOSED →
STATION_CLOSED → ROUTE_DISRUPTED → ROUTE_CAPACITY_EXCEEDED → INSUFFICIENT_INVENTORY → DISPATCH_CAPACITY_EXCEEDED →
DESTINATION_CAPACITY_EXCEEDED (all 409).

Lifecycle observed:
1. `POST` at tick T → 201, `status=PENDING`, `created_tick=T`. **Depot inventory is deducted immediately.**
2. Next `step` (T→T+1): `departure_tick=T`, `expected_arrival_tick=T+transit_ticks`, `status=IN_TRANSIT`.
   **If the route is DISRUPTED at that moment → `status=FAILED`, `failure_reason="ROUTE_UNAVAILABLE"`, and the
   depot inventory is NOT refunded (fuel lost).**
3. Arrives during the step that processes tick `expected_arrival_tick` (for transit 2, created at tick 0: arrives
   when the instance goes from tick 2 to 3; `actual_arrival_tick=2`). Station inventory increases.
4. **Arrival overflow:** if station inventory + quantity > capacity at arrival, only the room left is received
   (audit metadata `{"received": 173.15}` for a 2400 shipment). **The rest is lost.**
5. Arrival at a station in OUTAGE still delivers the fuel (inventory rises). During OUTAGE all demand at that
   station is unmet (`served_liters=0`).
6. Mid-transit route disruption: **[UNVERIFIED]**, probably no effect (the PDF says failure is checked at departure).
   Verify in phase 01.

Capacity checks:
- `DISPATCH_CAPACITY_EXCEEDED`: sum of quantities of this depot's allocations created/departing this tick, **across
  all fuels**, + new quantity > `dispatch_capacity_per_tick` (Gazipur 12,000, Patiya 11,000). Verified: 3000 + 6500
  OK, then +3000 PETROL → 409.
- `DESTINATION_CAPACITY_EXCEEDED` checks **current** station inventory + quantity > capacity. It **ignores
  in-flight shipments**, so we must do our own projected check or we will overflow.
- `ROUTE_CAPACITY_EXCEEDED`: quantity > route.max_shipment (per allocation; multiple allocations on the same route
  in the same tick are allowed, subject to the dispatch cap).
- `depot_constraint` event: depot status → CONSTRAINED but **dispatch capacity did NOT change** (11,000 stayed
  11,000) and shipping still works. Treat CONSTRAINED as a *risk signal*: our engine applies a self-imposed 50%
  dispatch cap and a larger reserve for constrained depots (documented policy, not a simulator rule).

Idempotency:
- Same key + same body → returns the existing allocation with **HTTP 201** (verified; the PDF table says 200, so
  accept both).
- Same key + different body → 409 `IDEMPOTENCY_KEY_MISMATCH`.
- Keys are never freed (even after cancel). **After `/admin/reset` the simulator DB is wiped, so keys can be reused
  there, but our DB still holds the old ones.** Include a `sim_run_id` in keys (see §7).

Cancel: only PENDING (i.e. within the same tick as creation). 200 → status CANCELLED, inventory refunded.

---

## 4. Events [VERIFIED]

`POST /admin/events {"type","start_tick","duration_ticks","parameters"}` → `end_tick = start_tick + duration_ticks`.

| type | parameters | effect while ACTIVE | on RESOLVE |
|---|---|---|---|
| demand_spike | multiplier (def 1.5), station_ids[], region_ids[] | station.demand_multiplier *= m | /= m |
| route_disruption | route_ids[] | route → DISRUPTED | AVAILABLE |
| station_outage | station_ids[] | station → OUTAGE, served = 0 | OPEN |
| depot_constraint | depot_ids[] | depot → CONSTRAINED | OPEN |
| shipment_delay | delay_ticks (def 2), depot_ids[], fuel_types[] | SupplyArrival.planned_tick += d, status DELAYED (one-shot) | no undo |
| supply_shortfall | factor (def 0.5), depot_ids[], fuel_types[] | SupplyArrival.quantity *= f (one-shot) | no undo |

- **Empty filter list = applies to ALL entities of that type.** A `route_disruption` with no `route_ids`
  disrupts every route.
- Events take effect at the next step where `tick >= start_tick`. If you inject with `start_tick` < current tick
  and `end_tick` < current tick, it starts and resolves almost immediately. **Always inject with
  `start_tick = current_tick` (or later).**
- `demand_multiplier` is visible in `/v1/stations` → a perfect real-time signal of active spikes.
- Future (SCHEDULED) events are visible in `/v1/events`. Our engine **may** use scheduled events for planning
  (e.g. avoid a route that will be disrupted at departure), since the simulator exposes them publicly. It must
  also work when events appear with no warning (surprise injects).

## 5. Faults [VERIFIED]

`POST /admin/faults {"type","duration_seconds" (0<x≤3600),"parameters"}`. Faults auto-expire (wall clock). Clear
with `POST /admin/faults/clear`. They affect `/v1/*` except `/v1/health`, and never `/admin/*`.

| type | effect |
|---|---|
| latency | sleeps `delay_ms` (def 500) before each request |
| unavailable | every request → 503 `{"error":{"code":"FAULT_INJECTED",…}}` |
| error_rate | probability `rate` (def 0.25) of 503 |
| stale_data | normal response + header `x-simulator-stale: true` |
| stream_disconnect | `GET /v1/stream` → 503 |

Organizers' surprise list also includes things we must defend against: **malformed/missing fields** in responses,
traffic spikes on our API, slow dependencies, and our prediction service going down.

## 6. SSE stream [VERIFIED]

```
: connected

event: allocation.status_changed
data: {full allocation json}

event: inventory.updated
data: {"entity_type":"depot","entity_id":"depot-patiya","inventory":{…}}

event: simulation.tick
data: {"tick": 11, "sim_time": "2026-01-01T02:45:00"}
```
- Also `simulator.notice` (`{"message":"Simulation reset"}` or `{"level":"error","message":…}`).
- `inventory.updated` is emitted for **depots only**. **Station inventory has no SSE event**, so re-GET stations on
  every `simulation.tick`.
- Keepalive comment `: keepalive` after 15 s of silence. Per-subscriber queue of 200: if we fall behind, we are
  silently dropped and must reconnect. No Last-Event-ID replay. **Always resync via REST after (re)connect.**

## 7. Reset detection (our rule)

A reset (`/admin/reset`) wipes the simulator. Detect it when **any** of these happen:
- SSE `simulator.notice` with message containing "reset"
- `instance.tick` decreases compared with our last seen tick
- `/v1/allocations` no longer contains an allocation id we know is in flight

On reset: create a new `sim_run` row (new `sim_run_id`), clear in-memory state, keep the history of old runs in the
DB (tagged with the old run id), and start fresh. Idempotency key format:
`fsip-{sim_run_id}-{decision_id}-{line_no}` (≤150 chars).

## 8. The world (baseline.yaml) [VERIFIED]

Regions: `region-dhaka` (demand_factor 1.00), `region-chattogram` (1.08).

| Depot | Region | Dispatch/tick | Capacity D/P/O | Initial D/P/O |
|---|---|---:|---|---|
| depot-gazipur | dhaka | 12,000 | 90,000 / 70,000 / 45,000 | 60,000 / 45,000 / 26,000 |
| depot-patiya | chattogram | 11,000 | 85,000 / 65,000 / 40,000 | 55,000 / 42,000 / 24,000 |

| Station | Region | Profile | Capacity D/P/O | Initial D/P/O |
|---|---|---|---|---|
| station-mirpur | dhaka | urban_high | 15,000 / 14,000 / 9,000 | 9,000 / 9,000 / 5,000 |
| station-tongi | dhaka | industrial | 18,000 / 9,000 / 6,000 | 11,000 / 6,000 / 3,500 |
| station-karnaphuli | chattogram | highway | 14,000 / 15,000 / 9,000 | 8,500 / 9,500 / 5,200 |
| station-coxsbazar | chattogram | regional | 12,000 / 12,000 / 7,000 | 7,500 / 7,500 / 4,200 |

| Route | Depot → Station | transit_ticks | max_shipment |
|---|---|---:|---:|
| route-gazipur-mirpur | gazipur → mirpur | 2 | 7,000 |
| route-gazipur-tongi | gazipur → tongi | 2 | 6,500 |
| route-patiya-karnaphuli | patiya → karnaphuli | 2 | 7,000 |
| route-patiya-coxsbazar | patiya → coxsbazar | 3 | 6,000 |
| route-gazipur-karnaphuli | gazipur → karnaphuli (cross-region) | 4 | 5,000 |
| route-patiya-mirpur | patiya → mirpur (cross-region) | 4 | 5,000 |

Note: **tongi and coxsbazar each have only ONE route.** A disruption on it means that station cannot be resupplied,
so pre-stock before a known disruption.

### Demand model (from engine source, line ~107) [VERIFIED]
```
demand_per_tick(station, fuel, tick) =
    daily_liters[profile][fuel] / 96            # 96 ticks/day at 15 min
  * hour_factor(profile, sim_time.hour)
  * region.demand_factor
  * station.demand_multiplier                    # changed by demand_spike
  * (1 + U(-noise, +noise))                      # deterministic RNG from (seed, tick, station, fuel)
```
(The `/96` scaling is inferred from the observed values, ~56-80 L per tick at Mirpur at night.)

| profile | DIESEL | PETROL | OCTANE | noise | busy hours (factor) | off-peak |
|---|---:|---:|---:|---:|---|---|
| urban_high | 8,500 | 10,500 | 5,600 | 0.10 | 07-09:59 & 16-20:59 → 1.45 | 0.70 |
| industrial | 14,000 | 4,500 | 2,200 | 0.08 | 06-17:59 → 1.55 | 0.45 |
| highway | 10,500 | 11,000 | 6,200 | 0.12 | 06-09:59 & 16-20:59 → 1.35 | 0.75 |
| regional | 7,200 | 7,600 | 3,600 | 0.10 | 07-20:59 → 1.25 | 0.65 |

Effective daily demand (multiplier 1): mirpur ≈ 23,370 L, tongi ≈ 20,700, karnaphuli ≈ 29,170, coxsbazar ≈ 19,870.
**Network total ≈ 93,100 L/day** (DIESEL ≈ 40,900, PETROL ≈ 34,300, OCTANE ≈ 17,900). Measured: 93,054 L in day 1.

Demand is generated even when inventory is 0: `demand_liters` is true demand and `unmet_liters` the shortfall.
**Forecast `demand_liters`, not `served_liters`.**

### Supply schedule (22 arrivals, all scenarios share it)
- Initial burst: supply-001 Gazipur D 18,000 @12; 002 Gazipur P 14,000 @16; 003 Patiya D 16,000 @14; 004 Patiya O 9,000 @20
- Recurring waves at +64 ticks: 101-106 @64-84, 201-206 @128-148, 301-306 @192-212.
  Per wave: Gazipur D 12,000 / P 10,000 / O 7,000; Patiya D 10,000 / P 8,000 / O 6,000.
- **The last arrival is at tick 212. After that there is no new fuel.**
- Supply arriving at a full depot is clipped at capacity (in the do-nothing run, depots ended exactly at capacity).

### Total fuel budget (initial depots + stations + all supply)
| Fuel | Total available | Daily demand | Days of cover |
|---|---:|---:|---:|
| DIESEL | ≈ 251,000 | ≈ 40,900 | ≈ 6.1 |
| PETROL | ≈ 187,000 | ≈ 34,300 | ≈ 5.5 |
| OCTANE | ≈ 115,900 | ≈ 17,900 | ≈ 6.5 |
Regional petrol: Chattogram ≈ 4.2 days, Dhaka ≈ 7.2 days, so we must **ship Gazipur petrol to Karnaphuli**
via the cross-region route before Patiya runs dry.

## 9. Measured baselines (3 simulated days = 288 ticks, stepping)

| Policy | Scenario | Service level d1 / d2 / d3 | Failures | Notes |
|---|---|---|---|---|
| Do nothing | baseline | 0.879 / 0.461 / 0.307 | 0 | first stockout: tongi DIESEL at tick 66 |
| Greedy (refill to 90% when projected < 60%) | baseline | 1.000 / 1.000 / 1.000 | 0 | depot PETROL Patiya down to 3,760 by day 3 |
| Greedy | combined crisis* | 1.000 / 0.989 / 0.992 | 0 | both depots PETROL at the 500 L reserve by day 3 |

\*Crisis: spike ×1.6 all stations for ticks 20-116, supply_shortfall 0.5 at tick 30, route_disruption
(patiya-karnaphuli, gazipur-mirpur) ticks 40-88, shipment_delay +24 at tick 50, tongi outage ticks 100-116.

Stepping 288 ticks with ~5 GETs per tick took ~8 s, so the harness is fast.

### Long horizon (600 ticks, past the end of supply), measured with `reference/policy_benchmark.py`
| Policy | Scenario | SL | Unmet L | Min station SL | Depot clipping L | Fuel left unused at end |
|---|---|---|---|---|---|---|
| Do nothing | baseline | 0.150 | 488,093 | 0.128 | **73,000** (supply lost at full depots) | 395,000 |
| Greedy | baseline | 0.947 | 30,319 | 0.879 | 0 | 10,225 |
| LP v4 (tuned) | baseline | **0.953** | **26,785** | **0.946** | 0 | 6,389 |
The world is deterministic: re-running gives identical numbers. Full matrix: `14_CODE_BLUEPRINTS.md` §5.
Scripts: `reference/baseline_do_nothing.py`, `reference/greedy_benchmark.py`, `reference/greedy_crisis.py`.

## 10. Audit log
`GET /admin/audit?limit=` gives the ground truth: `simulation.tick`, `allocation.created|departed|arrived|cancelled`,
`event.created|started|resolved`, `supply.arrived`, `admin.*`, `fault.*`. `allocation.arrived.metadata_json.received`
shows overflow loss. Use this in the evaluation harness to compute **overflow waste** precisely.
