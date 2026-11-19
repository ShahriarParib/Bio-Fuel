# 09: Live Demo Runbook (the brief's 14-step story)

Target length: 10-12 minutes + Q&A. Two people: **Driver** (clicks) and **Narrator** (talks). Rehearse 3 times.

## Pre-demo checklist (T-30 min)
- [ ] `make down && make up` on the demo machine; all containers healthy (`make ps`)
- [ ] `SIMULATION_SPEED=0.5` (1 sim day ≈ 3.2 min), `ENGINE_MODE=advisory`, `DEMO_CONTROLS=true`
- [ ] `make demo-reset` (simulator reset + new run), then **Run** the simulator from the Control page
- [ ] Browser tabs: (1) UI Overview, (2) Recommendations, (3) System Health, (4) Grafana Ops Overview,
      (5) Grafana Service Health, (6) the eval report `docs/evidence/evaluation.md`, (7) the architecture diagram
- [ ] Terminal ready with `scripts/chaos/kill_intelligence.sh` and `scripts/chaos/sim_unavailable.sh`
- [ ] Backup: recorded video of the full demo in case of venue network/machine problems

## Script
| # | Story step | What to show | Say (key line) |
|---|---|---|---|
| 0 | Intro (30 s) | architecture diagram | "Simulator is the world; our engine is the brain; REST is truth, SSE is a hint." |
| 1 | Normal operations | Overview: KPIs green, network graph, shipments moving | "Service level 100%, zero failed or wasted shipments." |
| 2 | Operator dashboard | Network page: inventory + forecast bands; Risk heatmap | "Every number is simulated and live, refreshed per tick." |
| 3 | Demand starts increasing | Control → "Demand spike Dhaka ×1.8" (or the organizer's inject) | |
| 4 | System detects risk | DEMAND_ANOMALY alert ("explained by event #N"), heatmap turns amber/red | "We detected it from data and matched it to the event." |
| 5 | Intelligence predicts shortage | Station detail: projected stockout 6 h, probability 72% | "Monte Carlo over the forecast's uncertainty." |
| 6 | Recommendation generated | Recommendations inbox card | "LP optimizer, constrained by dispatch, route and capacity limits." |
| 7 | Operator inspects | Explain panel: drivers, constraints, alternatives, confidence, narrative | "Human stays in control; low confidence always needs review." |
| 8 | Allocation simulated | What-if: do nothing vs plan → then **Approve** | "Expected 72% → 19%." |
| 9 | Crisis event | Control → "Combined crisis" (route disruption + supply delay) | |
| 10 | System adapts | Re-plan uses the cross-region route; scarcity mode banner if triggered; no FAILED allocations | "We never ship on a route that will be disrupted at departure, because that fuel would be lost." |
| 11 | Failure injected | Terminal: `kill_intelligence.sh` (and/or `sim_unavailable.sh 45`) | |
| 12 | Monitoring detects | System Health: prediction service Down, breaker OPEN, Grafana alert firing, incident opened | |
| 13 | Fallback activates | Banner "fallback model active"; decisions continue tagged "fallback"; for sim-down: cached state with age, no writes | |
| 14 | Operations continue | Restart service → breaker closes, incident resolved with timeline; KPIs recovered | "Detect in X s, mitigate in Y s, recover automatically." |
| + | Evidence (1 min) | Eval report table (do-nothing vs greedy vs optimizer), k6 report, CI pipeline green | "Measured, reproducible, deployable with one command." |

## Likely judge questions (prepare answers)
- Why LP and not RL? → measured comparison, explainability, constraints are hard limits, RL optional (phase 16 result).
- How do you avoid wasting fuel? → in-flight-aware capacity check, disruption-aware routing, depot overflow pre-push.
- What if the simulator lies (malformed data)? → validation + quarantine + last-good + DATA_QUALITY alert (drill F6).
- How does it scale? → stateless api behind cache; k6 numbers with 1 vs 3 replicas; single-writer engine by design.
- What happens after supply ends (tick 212)? → scarcity mode, fair rationing (max-min coverage), cross-region balancing.
- What's your reset/idempotency story? → sim_run_id in keys, replay-safe retries.
