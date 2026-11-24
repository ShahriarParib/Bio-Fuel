# Decisions (ADRs), assumptions and deviations

Why the platform is built the way it is, what we assumed, and every place where we changed the original design
(with the measurement that made us change it).

## ADRs
| # | Decision | Alternatives | Why |
|---|---|---|---|
| ADR-01 | Python/FastAPI backend | Node, Go | same ecosystem as the ML/optimizer libs; the simulator is FastAPI too; fastest to build |
| ADR-02 | Separate engine (single writer) / api (stateless) / intelligence services | monolith | independent failure (prediction-down drill), API scaling without double allocations, clear architecture story |
| ADR-03 | REST as truth, SSE as trigger + polling fallback | SSE-only state | the simulator has no replay, drops slow consumers, and has no station inventory event |
| ADR-04 | Redis snapshot cache + Postgres history | Postgres only | api p95 in ms under load; durable audit/history |
| ADR-05 | LP rolling-horizon optimizer with heuristic fallback | pure heuristic, RL | handles coupled constraints (dispatch shared across fuels, scarcity, cross-region), explainable, deterministic; heuristic as a safety net |
| ADR-06 | Structural seasonal forecast (hour-of-day profile × the simulator's demand multiplier × adaptive level) with calibrated 80 % intervals | LightGBM quantile model, deep learning, ARIMA | the demand process is profile × multiplier × bounded noise; this model reaches WAPE 5.8 % against a 4.5-5.8 % noise floor, so a heavier model has almost nothing left to win (see the B3 deviation below) |
| ADR-07 | Monte Carlo stockout probability | analytic formula | handles caps/outages/arrivals nonlinearity simply |
| ADR-08 | Shadow twin for what-if | calling the simulator | the simulator cannot be forked; the twin gives side-effect-free projections and alternatives |
| ADR-09 | Human-in-the-loop by default (advisory) + gated autopilot | full automation | brief §26 requires preserved human review; autopilot shows automation where it is safe |
| ADR-10 | LLM only for explanation, with a numeric grounding check and a template fallback | LLM decision-making; LLM-written summaries shown as-is | the brief says LLMs should support ops, not be a chatbot wrapper. We went one step further than "fallback": every number in a model's wording is checked against the figures the platform computed, and an answer that invents one is discarded. A fluent sentence with the wrong litre count is more dangerous than no sentence (`docs/evidence/assistant.md`) |
| ADR-11 | Docker Compose primary; Helm chart + kind as the optional second path (built in E1) | K8s first | "do not add infra complexity unless it improves the solution": judges run compose, and the chart has to earn its place by proving something compose cannot - self-healing and autoscaling (`docs/evidence/kubernetes.md`) |
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
8. Demo speed 0.5 tick/s; the engine also works at the default 8 tick/s (ticks are coalesced when it falls behind).
9. Overflow loss and failed-allocation loss are treated as waste KPIs (not exposed by `/v1/metrics`; computed from the
   audit/allocations).
10. Weekday effects: none in the simulator (verified from source: hour-of-day only); the `dow` feature is kept but
    expected to be irrelevant.

## Deviations log
| Date | Phase | Spec said | We did | Why |
|---|---|---|---|---|
| 2026-11-23 | B1 | sim_run id from a counter | `sim_runs` row resumed on engine restart, new on reset | counter restarted at 1 after a crash → idempotency key collisions |
| 2026-11-23 | B2 | recommendation expires at t+2 | `DECISION_TTL_TICKS` (default 8 = 2 sim h) + re-validation at approval (capacity-type violations trimmed, others dropped) | 2 ticks = 4 s at demo speed 0.5: no human can approve that fast |
| 2026-11-23 | B2 | new plan supersedes older pending | one open recommendation per (station, fuel); the planner skips keys already waiting for approval | superseding every tick would make an operator chase a moving target; skipping prevents double shipments |
| 2026-11-23 | B2 | disruption check at t+1 | blocked if a non-resolved disruption overlaps [t, t+1] | order of event activation vs departure inside a step is undocumented; one tick of margin is cheap |
| 2026-11-23 | B2 | api reads decisions from DB | api reads the engine's Redis document `fsip:decisions` (falls back to the engine) | history stays visible when the engine is down; reads scale with api replicas; no cross-service imports |
| 2026-11-23 | B2 | tokens `operator`/`supervisor` accepted in code | only `API_OPERATOR_TOKEN` / `API_SUPERVISOR_TOKEN` from the environment, constant-time compare | brief §19: no hard-coded credentials |
| 2026-11-24 | B3 | LightGBM + seasonal, choose by backtest | structural seasonal model only (LightGBM moved to extension E6 as a challenger) | the demand process is profile × multiplier × bounded noise; the seasonal model reaches WAPE 5.8% vs a ≈4.5-5.8% noise floor, so a GBM has almost no room to win in the time we have |
| 2026-11-24 | B3 | p10/p90 = 10/90% residual quantiles | 5/95% in-sample residual quantiles | in-sample residuals under-cover: measured 64% coverage of the 80% band; with 5/95% → 79% |
| 2026-11-24 | B3 | "risk before → after" = 12 h stockout probability | probability of a stockout from the arrival tick to +12 h, plus expected unmet liters, common random numbers | a station already dry showed "100% → 100%"; a shipment cannot change the risk before it arrives |
| 2026-11-24 | B3 | CUSUM from the first tick | unexplained-anomaly CUSUM starts after 96 ticks of history per series | during the first day unseen hours are model error, not anomalies (7 false alarms in the first test) |
| 2026-11-24 | B6 | F5 drill: inject `stream_disconnect` and watch the SSE drop | inject the fault, then force a reconnect (engine restart) during it | measured: the fault only refuses NEW `/v1/stream` connections; an open stream keeps delivering ticks, so the plain drill tests nothing |
| 2026-11-24 | B6 | api falls back to Postgres `world_snapshots` when Redis is down | api reads the live state from the engine (`/debug/state`, internal token); Postgres stays the last resort | the engine holds the live state in memory: fresher than the DB and no cross-service model import |
| 2026-11-24 | B6 | incidents in the `incidents` table | incidents held by the engine's HealthMonitor and published to Redis `fsip:incidents` (last 50) | an incident about the DB must be recordable while the DB is down; DB persistence of incidents is a B7 follow-up |
| 2026-11-24 | B6 | data age resets on every cycle | `age_seconds` = time since the last snapshot in which every resource was fresh | carried-forward (last-good) data showed "1.7 s old" during a 60 s outage (found by the F1 drill) |
| 2026-11-24 | B5→B6 | crisis 288 overflow = 0 | fixed in B6: the forecast's p10/p90 band is never narrower than ±12% until one full simulated day of history exists | the 80 L (one shipment, Mirpur PETROL, tick 37→41) came from a cold-start band fit in-sample on ~4 points per hour: far too narrow, so the LP filled the tank to the guard. Also added: a closed station draws no fuel in projections (`PlanningState.demand`), matching the simulator (served = 0, inventory flat) |
| 2026-11-24 | B6 | Redis client used directly | `fsip_common.redis_guard.GuardedRedis` (fail fast 5 s after an error, single probe) in engine and api | F10 drill: with Redis stopped, each reconnect's DNS lookup took ~8 s and starved the thread pool; the simulator breaker opened and forecasts timed out although both were healthy |
| 2026-11-24 | B6 | decision rows written synchronously | decision DB writes: 2 s timeout, then fail fast for 10 s (local ids meanwhile) | F9 drill: with Postgres stopped, each write hung ~8 s inside the cycle; ticks were coalesced and planning slowed |
| 2026-11-24 | B7 | error-budget policy: budget < 25 % → autopilot forced OFF | budget < 25 % → change freeze (policy switches need `override`, fault injects need a supervisor), operators paged by burn-rate alerts; autopilot keeps running | measured in the F1 drill: a 75 s outage spent the budget, the policy then switched the remedy off right after recovery and, with nobody approving, the service level fell 99 % → 81 %. SRE error-budget policy freezes risky changes; it does not switch off the fix |
| 2026-11-24 | B7 | API latency SLO "p95 < 200 ms" | SLI = share of `/api/state` requests under 250 ms (the nearest histogram bucket), target 99 % | a ratio SLI on existing buckets is cheaper and alertable; 200 ms is not a bucket boundary |
| 2026-11-24 | B7 | drills pass when health is NORMAL again | drills pass only when health is NORMAL **and** the decision inputs are no longer flagged degraded | a sticky `demand_fetch_failed` flag kept every decision at confidence 0.5 after an outage, so autopilot never executed again, while every drill reported "recovered" |
| 2026-11-24 | B7 | breaker opens after 5 consecutive failures | 5 consecutive failed **requests** (after retries), not attempts | per-attempt counting opened the breaker at a 50 % error rate and froze the engine for 15 s (F2 drill) |
| 2026-11-24 | B8 | k6 `decision_flow.js` (what-if) | `dashboard_read.js` models an open console (state, recommendations, alerts, status) + `spike.js` + `ratelimit.js` | there is no what-if endpoint; operator read traffic is what scales with users, writes are rare and human-paced |
| 2026-11-24 | B8 | deploy pulls from GHCR | `deploy.sh VERSION` pulls from GHCR, `--local` uses images built on the machine | the demo laptop must be able to deploy and roll back without network access to the registry |
| 2026-11-24 | E2 | assistant pinned to `gemini-3.1-flash-lite` | primary `gemini-3.5-flash-lite`, spare `gemini-flash-lite-latest` | the pinned model returned `503 UNAVAILABLE` repeatedly while we were building against it. A hosted model is a dependency like any other, so it gets the same treatment as the simulator: a measured alternative and a fallback that does not need it |
| 2026-11-24 | E2 | explanation cap 220 output tokens | 600 | measured: a real decision's facts produce ~150 tokens of prose, and 220 cut answers mid-sentence. Only generated tokens are billed, so the higher cap costs nothing and removes a failure mode. Answers that still stop early (`finishReason != STOP`) are discarded rather than shown |
| 2026-11-24 | E2 | probabilities passed to the model as 0-1 | converted to percentages before the model sees them | a model given 0.22 correctly writes "22%", which the grounding check would then reject as invented. Converting first keeps the guard strict without punishing true statements |
