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

<!-- New entries go here, one per block (B1-B8, E1-E9). Copy the template below. -->

## 5. What we discovered about the simulator
*(Written up on submission day, with the measurements behind each finding.)*

## 6. Requirement → evidence

| Brief § | Deliverable | Where | Status |
|---|---|---|---|
| 20.1 | Working application | `docker compose up -d --build` | in progress |
| 20.2 | Source repository | this repo + README | in progress |
| 20.3 | Simulator integration | `services/engine/app/sim/` | done (P01) |
| 20.4 | Intelligence component | forecast, risk, LP, twin | planned (B3, B5) |
| 20.5 | Operator interface | `frontend/` | planned (B4) |
| 20.6 | Architecture diagram | `docs/architecture.md` | in progress |
| 20.7 | Deployment | Compose, CI/CD, `scripts/deploy.sh` | planned (B8) |
| 20.8 | Observability evidence | `docs/evidence/observability.md` | planned (B7) |
| 20.9 | Resilience demonstration | `docs/evidence/resilience.md` | planned (B6) |
| 20.10 | Load-test evidence | `docs/evidence/loadtest-report.md` | planned (B8) |
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
