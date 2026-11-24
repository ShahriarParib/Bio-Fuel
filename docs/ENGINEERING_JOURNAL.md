# Engineering Journal: Fuel Supply Intelligence & Resilience Platform (FSIP)

**BUP CSE FEST 2026, Final Hackathon.** This journal records what we built, how and why we built it that way, which
tools we used, what went wrong and how we fixed it, and where the evidence is. It is updated at the end of every work
block, so it doubles as our demo notes and our answers for the judges' questions.

All results are from the organizer-provided **BUP Fuel Supply Simulator** (`asifmahmoud414/bup-fuel-supply-simulator:1.0.0`).
Nothing here describes real fuel infrastructure.

---

## 1. The problem as we understand it

- The world: 2 regions, 2 depots, 4 stations, 6 routes, 3 fuels, 15-minute ticks. The simulator owns the world; our
  platform observes it and may only act through `POST /v1/allocations` (depot → station shipments).
- What makes it hard is not forecasting accuracy. It is: fuel that is **lost** when a plan ignores the simulator's
  physical rules, supply that **stops** after ~2.2 simulated days (after which every litre is a rationing decision),
  crises that **combine**, and a platform that must **keep working** when its own parts fail.
- Our success measures: service level (served / demand), **0 litres destroyed** (failed or overflowing shipments), worst
  station service level (fairness), and SRE measures for the platform itself (availability, latency, time to detect
  and recover).

## 2. Toolbox (what we use, and why)

| Area | Tool | Why this one |
|---|---|---|
| Backend services | Python 3.12, FastAPI, httpx, pydantic v2 | same ecosystem as the optimizer/ML libraries; strict validation at every boundary |
| Simulator integration | async client + circuit breaker + retries with jitter, SSE listener + polling fallback | the simulator injects faults; REST is the truth, SSE is only a trigger |
| Time-series storage | TimescaleDB (Postgres 16) | per-tick history, replay, charts; plain Postgres compatible |
| Live state + events | Redis (snapshot cache + Streams) | millisecond reads for the UI under load; replayable event log |
| Forecasting | structural seasonal model (hour-of-day profile × live demand multiplier), conformal intervals | matches how demand is generated; reacts to spikes with zero lag; explainable |
| Risk | Monte Carlo projection (numpy) | stockout probability and ETA under forecast uncertainty, caps, arrivals and outages |
| Decisions | rolling-horizon LP (PuLP + CBC) with a greedy heuristic fallback | coupled constraints (shared dispatch caps, capacities, disruptions, scarcity) solved exactly; heuristic is the safety net |
| What-if | our own digital twin of the network | the simulator cannot be forked; the twin gives side-effect-free before/after numbers |
| Frontend | React 18 + TypeScript + Tailwind + TanStack Query + Recharts | fast to build, typed contracts with the api |
| Observability | Prometheus, Grafana, Loki, Alertmanager | metrics, dashboards, logs and alert routing that judges know |
| Load testing | k6 | scripted thresholds, JSON summaries, runs in Docker |
| Delivery | Docker Compose, GitHub Actions, GHCR, Trivy | one-command deploy; CI boots the real simulator and gates on decision quality |
| AI pair programmer | Claude Code | see §3 |

## 3. How we work

1. **Investigate before building.** We ran the simulator image and measured its behaviour (allocation lifecycle,
   capacity checks, event and fault semantics, demand model) before designing anything. Several rules that cause fuel
   loss are not in the documentation; our design is built around them (§5).
2. **Specify, then build in blocks.** A written spec (contracts, module specs, UI spec) and a sprint plan of 2-3 hour
   blocks. Each block ends with tests, a smoke test of the whole stack, a commit and an entry in this journal.
3. **AI-assisted, human-verified.** We use Claude Code to write code against our specs. The team decides the design,
   investigates the simulator, reviews every change, and accepts a block only when its acceptance commands pass on the
   real stack. Measured numbers in this journal come from real runs, not from the AI.
4. **Evidence over claims.** Every capability we present has a test, a drill script or a report in `docs/evidence/`.

## 4. Work log

### P00: Bootstrap (2026-11-19)
- **What:** repository skeleton, Docker Compose with the simulator, TimescaleDB, Redis, engine, intelligence, api and
  frontend; shared library `fsip_common`; CI workflow; docs skeleton.
- **How:** generated from a reviewed file-by-file spec, then unit-tested.
- **Evidence:** commit `1d875c0`; frontend 5/5 tests + build.
- **Criterion:** DevOps & Engineering Quality, Architecture.

### P01-P02: Simulator client, state sync, persistence, operator API (2026-11-20)
- **What:** resilient simulator client (timeouts, retries with jitter, circuit breaker, stale-header detection, two
  error envelopes), pydantic validation with quarantine, in-memory world state, tick-coalescing sync loop, SSE listener
  with reconnect, reset detection; TimescaleDB models, non-blocking write buffer, Redis snapshot + event stream;
  operator API with ETag caching and rate limiting.
- **Why this design:** the simulator can be slow, flaky, stale or reset at any time; the engine must never act on bad
  data and the UI must stay readable from cache.
- **Evidence:** commit `c4f3bc8`; 15 engine + 7 api + 1 intelligence unit tests.
- **Criterion:** Architecture & Integration, Resilience.

### B1: Make it real (2026-11-23, night)
- **Goal:** run the whole stack end-to-end for the first time against the real simulator image, and close the open
  questions about the simulator before building decisions on top of it.
- **What we built / fixed:**
  - Local platform: Docker Engine 29 + Compose inside WSL2 Ubuntu (moved to the D: drive), all images pre-pulled.
  - `.gitattributes` forcing LF: Windows checkouts had turned every shell script and the Makefile into CRLF, which
    breaks bash in WSL and in containers. `scripts/smoke_test.sh` also tolerates a CRLF `.env`.
  - `services/engine/app/db/models.py`: a `dict | list` annotation crashed SQLAlchemy at import, so the engine
    restarted in a loop.
  - **Simulation runs are now real database rows** (`db/repo.py: resume_or_create_sim_run`, `main.py: _on_run_start`).
    Before, the run id was an in-memory counter that restarted at 1 on every engine restart, so history writes
    failed their foreign key and, more dangerously, allocation idempotency keys (`fsip-{run}-{decision}-{line}`)
    would collide after a crash. Now a restarted engine resumes the open run; a simulator reset closes it and opens a new one.
  - **Demand ingestion** (`state/store.py`): the simulator returns demand history newest-first, and the ingest kept only
    rows newer than the last one seen, so each cycle stored a single row per series and dropped the rest. Now sorted
    oldest-first and persisted to TimescaleDB (12 rows per tick, verified).
  - Coalesced cycles computed the demand window after moving the tick cursor (always 1 tick): fixed in `state/sync.py`.
  - Health mode vs operating mode were mixed up (`engine_mode="advisory"`); fixed. HTTP client logs silenced to warnings.
  - Lint: 62 ruff findings fixed; captured real simulator fixtures for the contract tests.
- **How:** run, read the logs, fix the root cause, re-run, and only then trust a unit test. Six of these bugs passed
  all unit tests because the tests never touched a real database or a real simulator.
- **Simulator facts verified** (scripted against the image): fuel reaches the station at tick T+k+1; a route already
  disrupted at creation is rejected with 409 (nothing lost); a disruption during transit has no effect; the per-depot
  dispatch cap resets every tick and is shared by all fuels within a tick. The reference benchmark reproduced the
  planning numbers exactly (crisis, 288 ticks: service level 0.991), so the image is deterministic.
- **Tools:** Docker/Compose, WSL2, TimescaleDB `psql`, httpx scripts, pytest, ruff.
- **Evidence:** `SMOKE OK` (overall Healthy); 49 unit tests pass (26 common, 15 engine, 7 api, 1 intelligence); ruff
  clean; `sim_runs` resumed after `docker restart fsip-engine`; 120 demand rows for 10 ticks.
- **Judging criterion:** Architecture & Integration, DevOps & Engineering Quality, Resilience.
- **Demo line:** "Restart our engine mid-run and it resumes the same run: no duplicate or lost shipments."

### B2: Decide & act (2026-11-23, night)
- **Goal:** the first complete Decide → Act loop: recommendations an operator can approve, an autopilot that is only
  allowed to act when it is safe, and every outcome tracked back from the simulator.
- **What we built:**
  - `services/engine/app/decision/planning.py`: a pure planning view of the world (no I/O) with projection maths that
    follow the measured timing (fuel lands at tick t+k+1, demand of t..t+k−1 is consumed first).
  - `decision/constraints.py`: one feasibility check shared by every policy and every human edit. Besides the obvious
    rules it encodes three simulator behaviours we measured: a route disruption that starts at the departure step
    destroys the shipment (no refund), the dispatch cap is shared by all fuels of a depot within a tick, and fuel above
    capacity on arrival is lost while the simulator's own check ignores fuel already on the road.
  - `decision/heuristic.py`: greedy policy (most urgent station first, shortest safe route, fill to 90% on the median
    demand path, never above 98% on the low-demand path).
  - `decision/executor.py`: idempotent submission (`fsip-{run}-{decision}-{line}`; a retry after a timeout can never
    create a second shipment) and a mapping of every simulator answer to a line status.
  - `decision/engine.py`: plan each tick, record the decision with its reasons, autopilot gate (confidence ≥ 0.7, every
    line ≤ 7,000 L, fresh data, engine NORMAL), approval with re-validation on fresh data, reject, plan-now, mode and
    policy switches, outcome tracking (PENDING → IN_TRANSIT → ARRIVED/FAILED), audit log, Redis publication.
  - api: `/api/recommendations`, `/api/decisions`, approve/reject, `/api/plan-now`, `/api/control/mode|policy`,
    `/api/audit`. Removed hard-coded `operator`/`supervisor` tokens: only environment tokens, constant-time compare.
  - `evaluation/e2e_autopilot.py`: end-to-end quality check against the running stack using the simulator's own
    ground truth (service level, failures, overflow from its audit log). It becomes the CI quality gate in B8.
- **Why this approach:** a greedy policy already reaches 100% service while supply flows (measured during planning),
  so the value here is *never wasting fuel* and *keeping a human in control*. Every policy and every manual edit goes
  through the same constraint check; that is what makes "0 litres destroyed" a guarantee rather than luck.
- **Problems hit → fix:** the end-to-end test exposed that a simulator reset was never noticed while the simulator was
  paused (the poll only reacted to a *higher* tick); fixed, and reset notices from the event stream now trigger an
  immediate resync. WSL shut its VM (and every container) down after a minute without a terminal; fixed with
  `vmIdleTimeout=-1`. A 2-tick recommendation lifetime is 4 seconds at demo speed, so recommendations now live 8 ticks
  and are re-validated (trimmed or dropped) at approval.
- **Evidence:** autopilot, 192 ticks: service level **1.0**, unmet **0 L**, failed allocations **0**, overflow **0 L**
  (52 allocations, 201,810 L). Route cut injected for the next tick: 0 failures, the engine switched to the
  cross-region route. Approve/reject through the api with the actor in the audit log; approving twice → 409.
  Tests: 26 engine (incl. a hypothesis property test: any inventory, any demand → every planned line is feasible),
  5 auth.
- **Judging criterion:** Intelligence & Decision Quality, Working Product, Resilience, Security hygiene.
- **Demo line:** "Cut a route one tick before departure: the platform never ships into it, because that fuel would
  be destroyed. It reroutes across regions instead."

### B3: Predict & detect (2026-11-24, after midnight)
- **Goal:** see shortages coming early enough to act, know how sure we are, and keep predicting when the prediction
  service is gone.
- **What we built:**
  - `libs/fsip_common/forecast_seasonal.py`: a structural forecaster. Demand here is an hour-of-day profile × a live
    demand multiplier × bounded noise, so we model exactly that and learn every number from data (tests check the
    model *recovers* the day/night factors instead of hard-coding them).
  - `services/engine/app/intelligence/series.py`: the multiplier at every past tick is reconstructed exactly from the
    simulator's event list (start, end, factor), so history is normalised and a past spike does not inflate the
    profile; scheduled spikes enter the forecast before they start, active ones leave it when they end.
  - Intelligence service `POST /forecast` (validated input and output) + engine client with a 1.5 s timeout and a
    circuit breaker. On any error, timeout or invalid output the **same model runs inside the engine**; decision
    confidence drops to 0.6, below the autopilot gate, so a human reviews (brief §12: "model unavailable → fallback",
    "low confidence → human review"). `INTEL_FORCE_GARBAGE=true` is a drill that returns wrong numbers.
  - `risk/stockout.py`: Monte Carlo (300 correlated demand paths per station and fuel) with our in-flight arrivals and
    outage windows → probability of running dry in 12 h and before a truck can arrive, time to stockout, risk level.
    Recommendations carry the brief §9 card: inventory, expected demand, recommended litres, and *stockout risk from
    the arrival tick, without → with the shipment* (common random numbers, so the difference is the shipment only).
  - `alerts/manager.py` + `intelligence/pipeline.py`: deduplicated alerts that auto-resolve after 2 clear cycles.
    Detectors: stockout risk / occurred, depot overflow (supply that will not fit), supply delay and shortfall, route
    disruptions and outages *before they start* (single-route stations named), demand anomalies split into
    **explained** (visible multiplier / event) and **unexplained** (CUSUM on forecast residuals), data quality.
  - Network fuel cover (days of forecast demand per fuel) and hours of cover per station.
  - api: `/api/alerts`, `/api/alerts/{id}/ack`, `/api/risk`, `/api/cover`.
- **Problems hit → fix:** the first live run raised 7 "unexplained" alarms in stations the spike never touched: during
  the first simulated day the profile had not seen every hour, so model error looked like anomalies. Unseen hours now
  continue the nearest earlier hour, and the unexplained detector waits for one day of history. The impact first
  showed "100% → 100%" for a station that was already dry; it is now measured from the arrival tick. The 80% band
  covered 64% of actuals (in-sample residuals are optimistic); calibrated to 79%.
- **Evidence:** first stockout warning **40 ticks (10 h)** before the first real stockout in a do-nothing run (Tongi
  diesel, tick 65); forecast WAPE **5.8%** after two days against an estimated noise floor of ≈4.5-5.8% (a perfect model
  still has that error); band coverage **79%** for a target of 80%; a Dhaka demand spike produced "demand ×1.80
  (explained by event #1)" on the next tick; intelligence container stopped → fallback, confidence 0.6; restarted →
  back to the service. Example card: Tongi diesel, recommended 12,000 L, stockout risk **100% → 0%**, expected unmet
  **7,375 → 458 L**. Autopilot regression still 1.0 service level, 0 failures, 0 overflow. 78 unit tests.
- **Judging criterion:** Intelligence & Decision Quality, Resilience, Observability (forecast WAPE, coverage, fallback
  activations are Prometheus metrics).
- **Demo line:** "Our forecast error sits at the noise floor of the simulator itself, and when the prediction service
  dies the same model keeps running inside the engine, but the autopilot hands control back to a human."

### B4: Operator console (2026-11-24, night)
- **Goal:** an operations console where every number from B1-B3 is visible and the human can act.
- **What we built:** `frontend/src/pages/` Overview (KPIs with the simulator's own service level as ground truth,
  live network graph with route status and trucks on the road, station tank levels with fuel on the way, hours of
  cover, network fuel budget, forecast quality vs noise floor), Recommendations (each one in the brief §9 alert format:
  inventory, expected demand, recommended litres per depot, *stockout risk before → after*, unmet before → after, plus
  signals, constraints respected and alternatives), Risk heatmap (12 station-fuel cells), Alerts (open / resolved,
  acknowledge), Decisions (audit trail: who approved, every line from PLANNED to ARRIVED), System Health. Header: sim
  clock, engine mode, advisory/autopilot switch, live/stale indicator, operator sign-in; a degraded banner names the
  reason (stale data, prediction fallback). A permanent "SIMULATED ENVIRONMENT" strip (brief §26).
- **How:** React + TanStack Query polling the api's cached state every second (works even when the event stream is
  down), typed against the api contracts; colour is always paired with text (accessibility, projector-safe).
- **Problems hit → fix:** after a WSL restart the engine kept a CRITICAL "missing resources" alert forever because
  degraded reasons were never cleared when data came back (fixed + test). WSL still stopped its VM after longer idle
  periods; `scripts/windows/start-stack.ps1` starts a hidden keep-alive process and the stack in one command.
- **Evidence:** a headless browser (`scripts/demo/screenshots.py`) signed in, clicked *Approve & dispatch* on a live
  recommendation and the decision became EXECUTED with its lines submitted to the simulator; screenshots in
  `docs/evidence/screenshots/b4-*.png`; 9 frontend tests (network graph, alert card, approve disabled without a token).
- **Judging criterion:** Working Product & UX, Demo.
- **Demo line:** "This is the card from the brief, with real numbers: Cox's Bazar petrol, dry in 5.8 hours; ship
  8,550 litres from Patiya and the risk goes from 100% to 0%."

### B5: LP optimizer, policy switch, Impact Meter, evaluation matrix (2026-11-24)
- **Goal:** a real optimizer behind the same safety gate, a provable "value of the platform" number, and a measured comparison of policies.
- **What we built:** `services/engine/app/decision/optimizer.py` (rolling-horizon LP, PuLP 2.9 + CBC, p10-demand overflow path), runtime policy switch `POST /api/control/policy` (supervisor token), automatic heuristic fallback with `fsip_fallback_activations_total`, `intelligence/counterfactual.py` (exact do-nothing replay = Impact Meter), `evaluation/harness.py --matrix`, UI policy chip + Impact Meter panel.
- **How / approach:** the LP plans a horizon but only the first tick is executed; every line still passes `decision/constraints.py`, so the optimizer can never be less safe than the heuristic. If the solver fails or times out the heuristic answers and the card says "fallback policy". The Impact Meter replays the same world without our shipments (inventory minus real demand), so "litres of stockout prevented" is computed, not claimed.
- **Tools:** PuLP/CBC, asyncio.to_thread, the official simulator `/admin` endpoints (harness only).
- **Evidence:** `docs/evidence/evaluation.md` (15 runs, deterministic). 288 ticks: do-nothing SL 0.26-0.31 → heuristic/optimizer 0.99-1.00, 0 failed shipments in every run. 600 ticks (supply ends at tick 212, so a ceiling exists): optimizer lifts the worst station 0.868 → 0.929 (baseline) and 0.714 → 0.737 (crisis) at equal total SL, i.e. it spreads scarcity more fairly. Decide p95 72-205 ms. Impact Meter in the live run: without platform SL 0.461 (matches measured do-nothing), 100,452 L prevented.
- **Problems hit → fix:** optimizer test assumed 1,500 L was not urgent (it was) → test uses 300 L; crisis optimizer shows 80 L overflow from one arrival rounding against a demand dip (logged, below the 0.98 guard's tolerance; follow-up in B6).
- **Judging criterion:** intelligence component, decision quality, explainability.
- **Demo line:** "Same world, same seed: doing nothing serves 26% of demand; we serve 99%, and when fuel truly runs out the optimizer shares the shortage fairly."

### B6: Fail safely: measured health, mode machine, incidents, drills (2026-11-24)
- **Goal:** every failure in brief §11/§12 is detected, mitigated automatically, visible to the operator, and proven by a script that injects the real failure.
- **What we built:** `services/engine/app/state/health.py` (HealthMonitor: judges per component, mode machine NORMAL/DEGRADED/SAFE_HOLD/NO_FEASIBLE_ROUTES, IncidentTracker with hysteresis), `state/breakglass.py` (static plan served by nginx at `/breakglass/latest.html`), `chaos-proxy/` (corrupts simulator payloads on demand), api `routers/system.py` (`/api/system/status` from engine truth, `/api/incidents`, `/api/demo/*` controls behind the operator token), Redis-down fallback that reads state from the engine, UI System Health (Resilience Contract panel = brief §12's four rules live, incident timeline) and Control page (simulator clock, 7 event presets, 5 fault buttons, Demo Director checklist of the brief §23 story), `scripts/chaos/*.sh` + `run_all.sh`.
- **How / approach:** health is judged from **real traffic** (breaker, error ratio and p95 of the last 30 s, the stale header), because the simulator's `/v1/health` bypasses fault injection and answers "ok" while every real call fails. Judges and the mode machine are pure functions (unit tested); the monitor only samples every second. Incidents open after 2 bad samples and resolve after 3 good ones, so a 50% error rate is one incident, not thirty. Each drill measures from `/api/system/status`, i.e. what the operator actually sees.
- **Tools:** FastAPI, httpx, structlog, Prometheus gauges `fsip_engine_mode` / `fsip_component_status` / `fsip_incidents_total`, docker CLI for container drills, jq.
- **Evidence:** `docs/evidence/resilience.md` (generated). Simulator down 75 s: detected 1.6 s, SAFE_HOLD at 60.0 s, cached state served with its true age, recovered 8.4 s after the fault ended. Malformed stations: rejected, 16 payloads quarantined, alert names the field `stations.0.inventory`.
- **Problems hit → fix:** (1) data age showed "1.7 s old" during a 60 s outage because carried-forward data reset the clock → age counts from the last fully fresh snapshot; (2) `stream_disconnect` only refuses new connections → the drill forces a reconnect; (3) the break-glass volume was created root-owned by nginx → mounted `nocopy` on the frontend so the engine image seeds the ownership; (4) **cascading failure found by the Redis drill:** with Redis stopped, every command reconnected and each DNS lookup for the dead container took ~8 s, starving the thread pool, so the simulator breaker opened and forecasts timed out although both were healthy → `GuardedRedis` (fail fast, one probe per 5 s) in engine and api; (5) the DB drill showed the same pattern inside the decision cycle → 2 s timeout + 10 s fail-fast on decision writes (ticks kept full pace: 15 in 30 s, 34 writes buffered and flushed); (6) the B5 leftover: the 80 L crisis overflow was one shipment at tick 37, planned on a cold-start forecast band fit in-sample on ~4 points per hour → band never narrower than ±12% during the first simulated day; crisis optimizer now 0 L overflow at the same service level. A closed station now also draws no fuel in projections (simulator: served = 0 during an outage).
- **Judging criterion:** resilience, observability, operator experience.
- **Demo line:** "Kill anything you like: we detect it in about two seconds, tell you exactly what we are doing about it, and recover on our own. Here is the stopwatch."

### B7: Observe: metrics, logs, alerts, SLOs with a domain SLO, dashboards as code (2026-11-24)
- **Goal:** an operator (or a judge) can see what the platform is doing, get paged when it matters, and trust that "recovered" means recovered.
- **What we built:** compose `obs` profile (Prometheus, Grafana 11.2, Alertmanager, Loki + Promtail, cAdvisor, node-exporter, all pinned); `observability/prometheus/alerts.yml` (16 rules) and `slo.yml` (4 SLOs incl. the **domain SLO "fuel service level ≥ 99 %"**, budget recording rules, multi-window burn-rate alerts 14.4× 5m/1h and 6× 30m/6h); Alertmanager → api webhook (`/api/internal/alertmanager`, internal token rendered at container start) → SYSTEM alerts on the Alerts page; `services/engine/app/state/slo.py` (the engine computes the domain SLO per tick and enforces the error-budget policy without depending on Prometheus); 4 dashboards generated by `observability/grafana/gen_dashboards.py` (Ops, Service Health, Intelligence, Logs & Incidents); `scripts/check_metrics.sh`; UI SLO panel + Grafana links.
- **How / approach:** dashboards are code (one generator, reviewed in git, consistent units), not hand-exported JSON. Alerts and SLOs are computed from the same real-traffic metrics as the engine's health judges. Every alert travels the full path an operator would see (Prometheus → Alertmanager → api → UI).
- **Tools:** Prometheus 2.55, Alertmanager 0.27, Grafana 11.2, Loki/Promtail 3.2, cAdvisor 0.49, node-exporter 1.8, Playwright for dashboard screenshots.
- **Evidence:** `docs/evidence/observability.md`; `check_metrics.sh` → METRICS OK (44 metrics, 7 targets up, 4 SLO budgets, all rules healthy); 75 s simulator outage → SimulatorDown + EngineSafeHold fired in the UI and resolved by themselves; with the fixes below autopilot held the service level at 100 % through the same outage.
- **Problems hit → fix (the drills found real bugs again):**
  1. **The error-budget policy from the spec backfired.** "Budget < 25 % → autopilot off" turned a 75 s outage into a collapse: the outage spent the budget, autopilot was switched off right as the simulator came back, nobody approved, service level 99 % → 81 %. Changed to the actual SRE meaning of an error-budget policy: **change freeze** (policy switches need an explicit override, chaos injects need a supervisor), operators paged, the remedy keeps running.
  2. **A sticky degraded flag silently disabled autopilot after every outage.** `demand_fetch_failed` was set during the outage and never cleared, so every later decision had confidence 0.5 (< 0.7 gate) and autopilot never executed again, while the health page said NORMAL and **every resilience drill reported "recovered"**. Fixed the flag (and `sim_run_unregistered`, which also never cleared and is not a data problem), added a regression test, and the drills now count recovery only when the decision inputs are trusted again.
  3. Rate limiting keyed on the client IP, which behind nginx is always the proxy: all users shared one 600 req/min bucket → `X-Forwarded-For`.
  4. PromQL: with zero 5xx there are no 5xx series, so availability SLIs were empty instead of 0 (`or vector(0)`); the latency SLI read 100 % errors with no traffic → rewritten as slow/total.
  5. Headless Chromium in WSL has no locale and Grafana crashed on boot ("Incorrect locale information") → screenshots set `locale`.
  6. node-exporter's `rslave` mount fails on WSL2 (root is not a shared mount) → plain read-only mount.
  7. **The circuit breaker treated a flaky simulator as a dead one.** It counted every retry attempt as a failure, so at a 50 % error rate five failed attempts in a row were common, the breaker opened and the engine froze for 15 s (ticks 368 → 368) although half the calls worked. The stricter F2 drill caught it. Now one failure per request after its retries (≈ 6 % of requests fail at 50 % attempt errors); ticks keep full pace (498 → 505 in 15 s), and a dead simulator still opens the breaker within one snapshot.
- **Judging criterion:** observability, resilience, DevOps maturity, honesty of evidence.
- **Demo line:** "Our SLO is about fuel reaching people, not just HTTP 200s. And when the budget is spent we freeze changes, not the trucks."

### B8: Ship: CI/CD with a decision-quality gate, deploy with automatic rollback, load test (2026-11-24)
- **Goal:** the brief's §13 chain (Source → Build → Test → Package → Deploy → Health Check → Running) as real, measured automation, plus load-test evidence.
- **What we built:** `.github/workflows/ci.yml` (lint + unit for Python and TypeScript → images built once → e2e with the **official simulator image** → smoke → **decision-quality gate**: harness baseline 96 ticks for both policies, SL ≥ 0.99, 0 failed shipments, 0 overflow → metrics contract → **Trivy** (fail on fixable CRITICAL) → on `v*` tags the *same* images pushed to GHCR (`:vX.Y.Z`, `:sha`) and a GitHub Release with the evidence reports); `scripts/deploy.sh` (resolve images → roll out app services only → health gate: containers running, crash-loop fast fail, api reports the new version, engine ready, system not Down, smoke → record or **automatic rollback** to the last good version), `scripts/release_local.sh`, `scripts/demo/make_bad_release.sh`; k6 scenarios (`loadtest/k6/`) + runner + report generator; README, architecture (Mermaid), runbook.
- **How / approach:** the gate that matters for this product is decision quality, not only "tests pass": a change that makes the planner ship less, fail a shipment or overflow a tank cannot merge. Images are built once and promoted (what was tested is what ships). Deploy touches only the app services; data stores and the simulator keep running.
- **Tools:** GitHub Actions, buildx, Trivy 0.57, GHCR, softprops/action-gh-release, k6 0.54, jq.
- **Evidence:** CI run green on e2e + both decision gates; rollback demo: `v1.0.0-rc1` deployed (gate 9 s) → `v0.9.9-bad` (api exits on boot) rejected → rolled back, healthy in 9 s, history `deploy/versions.env`; `docs/evidence/loadtest-report.md`: api ×3 holds 2-4× the load of ×1 at the same latency, spike 10 → 300 VUs p95 17.7 ms with 0 errors, rate limit returns 429 + Retry-After and never 5xx, engine 0 ticks behind in every run.
- **Problems hit → fix:**
  1. **Trivy failed the first pipeline** (the gate working): `python-jose` 3.3.0 (CVE-2024-33663) in the api, never imported → removed; OpenSSL/brotli CVEs in the pinned nginx Alpine base → `apk upgrade` in the image.
  2. **The load test found the api's limits** (knee at 50 VUs per 0.5-CPU replica, 1.5 % errors at 200 VUs). Hot path rewritten: content-hash ETag checked before reading the document, cached bytes returned as is, one-round-trip rate limiter. Spike p95 96.8 → 17.7 ms, errors at 200 VUs 1.5 % → 0; the knee itself did not move (fixed per-request cost on 0.5 CPU), scaling out does.
  3. **A correctness bug hiding in the ETag:** `run-tick` ETags let browsers revalidate to 304 while mode/health changed within a tick, so the console could miss SAFE_HOLD during an outage. Content hash + test.
  4. The rate limiter trusted the leftmost `X-Forwarded-For` entry (client-forgeable) → rightmost (set by our nginx); 429s now carry `Retry-After`.
  5. `.env` set `APP_VERSION=dev` inside every container, so a deploy could not prove which version runs → compose passes the deployed version explicitly; the gate checks it.
  6. The api's Postgres fallback imported engine code that is not in the api image (always failed) → removed; Redis down → engine directly.
- **Judging criterion:** DevOps (delivery workflow, rollback, security scan), load testing, architecture, documentation.
- **Demo line:** "This release breaks the API. Watch: the deploy notices in seconds and puts the last good version back, and you never see it."

### E1: Kubernetes — self-healing and autoscaling, with the single-writer invariant kept intact (2026-11-24)
- **Goal:** prove the platform does not just *run* but *keeps* running without a human: a dead component is replaced and the read path scales with demand — while the one thing that must never be replicated (the engine) stays single.
- **What we built:** `deploy/kind/cluster.yaml` (single-node cluster, node ports 30080/30800 → host 3080/3800, `serverTLSBootstrap` so metrics-server can scrape over real TLS); Helm chart `deploy/helm/fsip` (Chart, values, `_helpers.tpl`, ConfigMap+Secret, Redis/Postgres StatefulSets, simulator, engine + break-glass sidecar, intelligence, api + HPA + PDB, frontend + nginx ConfigMap + PDB, NOTES.txt); `scripts/k8s/{lint,up,demo,down}.sh`; Makefile `k8s-lint k8s-up k8s-demo k8s-ps k8s-down`; runbook and architecture sections (two runtime topologies side by side).
- **How / approach:** the chart encodes decisions we can defend, not defaults. The api is the only service that scales out because it is the only stateless one, and its HPA numbers come from the B8 load test (~50 concurrent consoles per 0.5-CPU replica → 2–6 replicas at 60 % CPU, `scaleUp` stabilization 0 so a surge is answered on the next metrics window, `scaleDown` 120 s + one pod/minute so a dip cannot flap). The engine is pinned `replicas: 1, strategy: Recreate` — a RollingUpdate would run two writers for a few seconds, and two writers can dispatch the same fuel twice. `scripts/k8s/lint.sh` asserts that invariant on the *rendered* manifests, so a future edit that scales the engine fails before it reaches a cluster.
- **Tools:** kind 0.24 (Kubernetes 1.31), Helm 3.16, kubectl 1.31, metrics-server 0.7.2, k6 0.54, nginx 1.27-alpine (sidecar).
- **Evidence:** `docs/evidence/kubernetes.md`, generated by `make k8s-demo`. Proof 1: intelligence pod deleted → `prediction_service: Down`, `engine_mode: DEGRADED`, **0 failed status requests**, replacement pod Ready in **7 s** (stable across runs), forecaster Healthy and engine back to NORMAL in **8-16 s** (it depends where the deletion falls in the engine's probe cycle). Proof 2: 300-VU spike → api **2 → 5-6** replicas (peak 150-170 % CPU of request), **0 5xx** in ~21 400 requests, p95 **7.7-8.1 ms**, back to 2 replicas in 257-321 s as the scale-down policy dictates. 21 rendered objects pass `helm lint` + schema + invariant checks.
- **Problems hit → fix:**
  1. **Three chart bugs caught before any cluster existed**, by rendering instead of installing: one shared `fsip.env` helper emitted `envFrom` inside an `env:` list (invalid YAML); `frontend.yaml` computed its config checksum by including *itself*, which Helm follows as infinite recursion; and nginx `resolver` was given a DNS *name*, which it rejects — it needs the cluster DNS address. Split into `fsip.env`/`fsip.envfrom`, moved the nginx config into a named template, made the resolver IP a value.
  2. **The break-glass page had quietly disappeared.** Under Compose the frontend mounts the engine's volume; in Kubernetes those are different pods, so `/breakglass/` would 404 exactly when it is needed. Fixed properly rather than dropped: an nginx sidecar in the engine pod serves the shared `emptyDir`, behind a Service with `publishNotReadyAddresses: true` — because the whole point is to answer while the pod is unhealthy. Its readiness probe had to target `/`, not `/breakglass/`, since that directory has no index and nginx answers 403 by design.
  3. **The first self-heal measurement was wrong in our favour and we rejected it.** The script read the label selector's first pod, which is the *terminating* one — still Ready — so it reported "replacement ready in 0 s". Now only a pod whose name differs from the deleted one counts: the honest answer is 7 s.
  4. **Peak replicas were under-reported (4, then 5, actually 6)** because the autoscaler is still adding pods at the moment k6 exits. Peaks are now read from the complete timeline, after the scale-down observation.
  5. **k6 reported 95 % "failed" requests and the platform was fine.** `http_req_failed` counts every non-2xx, and our hot path answers `304 Not Modified` from the state ETag. The number that matters is the explicit `not 5xx` check (0 failures); the report now says so instead of hiding the 95 %.
  6. Smaller ones: the k6 image runs as a non-root user and could not write its summary into the log directory; `Path.write_text` on Windows turned our LF scripts into CRLF and bash rejected `set -o pipefail\r`; kind's kubelet serving CSRs need approving before metrics-server can scrape, otherwise the HPA reports `FailedGetResourceMetric` forever.
- **Judging criterion:** DevOps maturity and deployment, resilience, architecture (stated invariants), honesty of evidence.
- **Demo line:** "Watch me kill the forecaster. The platform keeps deciding and says it is on the fallback — and seven seconds later Kubernetes has put it back. Now watch three hundred operators log in at once: the API grows from two replicas to six by itself, no errors, then shrinks back."

<!-- New entries go here, one per block (B1-B8, E1-E9). Copy the template below. -->

## 5. What we discovered about the simulator
*(Written up on submission day, with the measurements behind each finding.)*

## 6. Requirement → evidence

| Brief § | Deliverable | Where | Status |
|---|---|---|---|
| 20.1 | Working application | `docker compose up -d --build` | done |
| 20.2 | Source repository | this repo + README | done |
| 20.3 | Simulator integration | `services/engine/app/sim/` | done (P01) |
| 20.4 | Intelligence component | forecast, risk, LP, twin | done (B3, B5) |
| 20.5 | Operator interface | `frontend/` | done (B4) |
| 20.6 | Architecture diagram | `docs/architecture.md` | done (B8) |
| 20.7 | Deployment | Compose, CI/CD, `scripts/deploy.sh` | done (B8) |
| 20.8 | Observability evidence | `docs/evidence/observability.md` | done (B7) |
| 20.9 | Resilience demonstration | `docs/evidence/resilience.md` | done (B6) |
| 20.10 | Load-test evidence | `docs/evidence/loadtest-report.md` | done (B8) |
| 20.7 | Orchestration (beyond the brief) | Helm chart + kind, `docs/evidence/kubernetes.md` | done (E1) |
| 20.11 | Final demo | `docs/demo-runbook.md` | planned |

## 7. Questions we expect, and our answers
*(Filled in as decisions are made; see also `docs/assumptions.md`.)*

---

## Entry template
```markdown
### B<N>: <title> (<date>, <hh:mm>-<hh:mm>)
- **Goal:** one sentence.
- **What we built:** bullets with file paths.
- **How / approach:** the method and why it beats the obvious alternative.
- **Tools:** libraries, services, scripts.
- **Evidence:** test counts, measured numbers, screenshot paths, commit hash.
- **Problems hit → fix:** what broke and how we found and fixed it.
- **Judging criterion:** which row(s) of brief §24 this serves.
- **Demo line:** the one sentence we say when showing it.
```
