# Project status

Release: **v1.0.0-rc1** (all core work done). Deadline: Thu 2026-11-26. The full story of each step, including every
bug the tests and drills found, is in [docs/ENGINEERING_JOURNAL.md](docs/ENGINEERING_JOURNAL.md).

## Work blocks

| Block | Done | Evidence |
|---|---|---|
| B1 Make it real | 2026-11-23 | full stack up, `SMOKE OK`; simulator semantics verified (below); benchmark reproduced |
| B2 Decide & act | 2026-11-23 | autopilot 192 ticks: service level 1.0, 0 unmet, 0 failed shipments, 0 overflow; approve/reject with audit; a route cut at departure → 0 FAILED, the cross-region route used |
| B3 Predict & detect | 2026-11-24 | stockout warning 10 h before the first stockout; forecast WAPE 5.8 % (noise floor ≈ 4.5-5.8 %), 80 % interval coverage 79 %; demand spike → "explained" alert next tick; prediction service down → fallback model, back → primary |
| B4 Operator console | 2026-11-24 | Overview, Recommendations (brief §9 format, risk before → after), Risk, Alerts, Decisions, System Health; approve clicked in a real browser → executed in the simulator; screenshots `docs/evidence/screenshots/b4-*.png` |
| B5 Optimizer + Impact Meter | 2026-11-24 | LP rolling horizon + heuristic fallback; exact do-nothing counterfactual; [evaluation.md](docs/evidence/evaluation.md): 3 days 26-31 % → 99-100 % served, 0 failed shipments, 0 overflow |
| B6 Fail safely | 2026-11-24 | health from real traffic, mode machine, incidents, chaos proxy, break-glass plan, Control page; [resilience.md](docs/evidence/resilience.md): 11/11 drills pass |
| B7 Observe | 2026-11-24 | Prometheus, Alertmanager, Loki, Grafana (4 dashboards as code), 16 alerts, 4 SLOs incl. the domain SLO; [observability.md](docs/evidence/observability.md) |
| B8 Ship | 2026-11-24 | CI with e2e on the official simulator image, decision-quality gate, Trivy, GHCR + release; `deploy.sh` with automatic rollback; [loadtest-report.md](docs/evidence/loadtest-report.md) |

## Simulator behaviour we verified (image `asifmahmoud414/bup-fuel-supply-simulator:1.0.0`)

- **Arrival timing** (created at tick T, transit k): PENDING at T; IN_TRANSIT from instance tick T+1 with
  `departure_tick = T`, `expected_arrival_tick = T+k`; ARRIVED and station inventory up at instance tick **T+k+1**.
- **Route disrupted at creation** → 409 `ROUTE_DISRUPTED` (rejected, nothing lost). A disruption that **starts at the
  departure tick** → FAILED, fuel lost, no refund. A disruption that starts **mid-transit** has no effect.
- **Dispatch capacity** resets every tick and is shared across all fuels of a depot (6,000 + 6,000 DIESEL ok, +500
  PETROL → 409 `DISPATCH_CAPACITY_EXCEEDED`).
- **Arrival overflow** is silently lost; the simulator's own capacity check ignores fuel already in transit.
- **Station outage**: demand is recorded as unmet but the inventory does not fall.
- **`/v1/health`** ignores injected faults; **`stream_disconnect`** only refuses new SSE connections.
- **Deterministic**: the same seed and the same actions give the same numbers.

## Key numbers

| Metric | Value | Where |
|---|---|---|
| Service level, 3 days, do nothing → platform (normal / crisis) | 31 % → 100 % / 26 % → 99 % | evaluation.md |
| Failed shipments / overflow litres, every scenario | 0 / 0 | evaluation.md |
| Forecast WAPE / interval coverage | 5.8 % / 79 % | journal B3 |
| Stockout warning lead time | 40 ticks (10 h) | journal B3 |
| Optimizer decision time p95 | 72-260 ms | evaluation.md |
| Failure drills passed / detect / recover | 11 of 11 / 0.6-9 s / 0-25 s, no human action | resilience.md |
| Spike 10 → 300 users, `/api/state` p95 | 17.7 ms, 0 errors | loadtest-report.md |
| Bad release → rolled back and healthy | 9 s | journal B8 |
