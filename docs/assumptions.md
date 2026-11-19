# 10: Decisions (ADRs) and Assumptions

Copy this into the repo as `docs/decisions.md` + `docs/assumptions.md` in phase 15, updated with deviations.

## ADRs
| # | Decision | Alternatives | Why |
|---|---|---|---|
| ADR-01 | Python/FastAPI backend | Node, Go | same ecosystem as the ML/optimizer libs; the simulator is FastAPI too; fastest to build |
| ADR-02 | Separate engine (single writer) / api (stateless) / intelligence services | monolith | independent failure (prediction-down drill), API scaling without double allocations, clear architecture story |
| ADR-03 | REST as truth, SSE as trigger + polling fallback | SSE-only state | the simulator has no replay, drops slow consumers, and has no station inventory event |
| ADR-04 | Redis snapshot cache + Postgres history | Postgres only | api p95 in ms under load; durable audit/history |
| ADR-05 | LP rolling-horizon optimizer with heuristic fallback | pure heuristic, RL | handles coupled constraints (dispatch shared across fuels, scarcity, cross-region), explainable, deterministic; heuristic as a safety net |
| ADR-06 | Seasonal-profile + LightGBM quantile forecast, chosen by backtest | deep learning, ARIMA | the demand process is seasonal × multiplier × bounded noise; quantiles give the uncertainty for stockout probability |
| ADR-07 | Monte Carlo stockout probability | analytic formula | handles caps/outages/arrivals nonlinearity simply |
| ADR-08 | Shadow twin for what-if | calling the simulator | the simulator cannot be forked; the twin gives side-effect-free projections and alternatives |
| ADR-09 | Human-in-the-loop by default (advisory) + gated autopilot | full automation | brief §26 requires preserved human review; autopilot shows automation where it is safe |
| ADR-10 | LLM only for explanation/summaries, with template fallback | LLM decision-making | the brief says LLMs should support ops, not be a chatbot wrapper; deterministic decisions |
| ADR-11 | Docker Compose primary; K8s/Helm optional | K8s first | "do not add infra complexity unless it improves the solution"; judges run compose |
| ADR-12 | Prometheus/Grafana/Loki | ELK | lighter; Grafana is already on the dev machine |
| ADR-13 | k6 for load tests | Locust | scriptable thresholds, JSON summary, docker image |
| ADR-14 | Use publicly visible SCHEDULED events in planning | ignore them | they are part of the public API state; still robust to unannounced injects |
| ADR-15 | Self-imposed 50% dispatch cap for CONSTRAINED depots | ignore status | the simulator does not reduce the cap, but the status "signals reduced capacity"; conservative and documented |

## Assumptions (where the brief/guide was silent; decided by us)
1. Tick = 15 sim minutes; 96 ticks/day. Code reads `tick_minutes` from `/v1/instance` and never hard-codes it.
2. Station priority is equal by default (no criticality data provided); configurable via `config/priorities.yaml`.
3. Transport cost ∝ transit_ticks × liters (no cost data provided).
4. No ordering/procurement from outside: the supply schedule is fixed; we only allocate depot → station.
5. Safety stock target = z·σ·√L with z = 1.65 (95%) + 4 h of p50 demand.
6. "Projected stockout" horizon = 12 h (48 ticks); scarcity look-ahead = 24 h (96 ticks).
7. Operator auth = static bearer token (hackathon scope); roles: viewer, operator.
8. Demo speed 0.5 tick/s; the engine also works at the default 8 tick/s (coalescing; verified in phase 12).
9. Overflow loss and failed-allocation loss are treated as waste KPIs (not exposed by `/v1/metrics`; computed from the
   audit/allocations).
10. Weekday effects: none in the simulator (verified from source: hour-of-day only); the `dow` feature is kept but
    expected to be irrelevant.

## Deviations log (the executing AI appends here)
| Date | Phase | Spec said | We did | Why |
|---|---|---|---|---|
