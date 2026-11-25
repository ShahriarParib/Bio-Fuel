# Poridhi Puku CLI — Real-World Capability & SRE Evaluation Report

**Subject:** `Poridhi Puku CLI` (puku-cli) evaluated against the Fuel Supply Intelligence & Resilience Platform (FSIP) — BUP CSE FEST 2026 Final Hackathon.
**Repository:** `D:\Fuel Project\fuel-platform`
**Evaluation date:** 2026-11-26
**Evaluator:** the Puku CLI agent itself, working from the repository only (no pre-briefing beyond `PUKU.md`).

---

## Benchmark Scorecard

| Dimension | Score (1–5) | Basis |
|---|:--:|---|
| **Context Retrieval** | **5** | `PUKU.md` was auto-discovered and injected at session start, giving the project overview, run commands (`make test`, `make chaos`, `scripts/smoke_test.sh`) and engineering rules before any tool call. Deep file reads then retrieved exactly the four artifacts under test in a single pass, with no keyword-stuffing or repeated searching. |
| **Accuracy** | **5** | Every claim in this report is traceable to a file and line range, and cross-file facts were confirmed rather than inferred (e.g. the 0.98 overflow guard is defined in `constraints.py:15` *and* independently re-declared in `optimizer.py:28`; the fallback path was confirmed in `decision/engine.py:140-154`, not just the `optimizer.py` docstring). |
| **Code Reasoning** | **5** | Traced a multi-file invariant end to end: `planning.py` projection maths → `constraints.py:101-110` budget accounting → `optimizer.py:107` LP constraint → `heuristic.py:110` quantity clamp. Correctly distinguished the two different capacity semantics (`room_now` = simulator's check, ignores in-flight; `room_at_arrival` = ours, includes in-flight) — a distinction the source code explicitly warns about in its own docstrings. |
| **SRE Usability** | **4** | Excellent for read-and-explain triage (the drift between `docs/evidence/resilience.md` and the journal's B6 narrative was surfaced immediately, and the "recovery counted from fault expiry" caveat in the table's `Note` column was interpreted correctly). One point held back: the CLI has no native awareness of *this* platform's operational surface — it could not, unprompted, tell me that `bash scripts/chaos/run_all.sh` regenerates the resilience report, nor which container the drill targets. |

**Overall:** 19 / 20. The tool demonstrated that it can act as a credible reviewer of a distributed decision platform — not merely a code-summariser — which is exactly the "AI-assisted, human-verified" workflow the journal claims in §3.

---

## Detailed Findings

### 1. Deep Code Comprehension — The arrival-capacity check in `constraints.py`

**File:** `services/engine/app/decision/constraints.py`

#### The problem the check solves

The simulator's own capacity test is naive: it compares **current** station inventory against capacity and ignores anything already on the road. But fuel physically lands later, so a shipment that "fits" today can overflow the tank on arrival — and the surplus is silently destroyed, not refunded. This is recorded as *simulator finding #2* in `docs/ENGINEERING_JOURNAL.md:365` ("The capacity check ignores fuel already in transit") and is encoded as **rule 7** in the module docstring (`constraints.py:6`).

#### How the check accounts for in-flight fuel

Two distinct budget methods model the two distinct realities:

| Method | Location | Semantics | In-flight? |
|---|---|---|---|
| `room_now()` | `constraints.py:112-115` | Mirrors the **simulator's** check: `capacity - current inventory` | **No** |
| `room_at_arrival()` | `constraints.py:101-110` | Mirrors **physical reality** at the arrival tick | **Yes** |

`room_at_arrival` is the load-bearing one, and it composes four quantities:

1. **Projected inventory before arrival** — `inventory_before_arrival(self.state, key, transit, low=True)` (`constraints.py:106`). In `planning.py:118-127`, this walks a full demand-and-arrival projection for `transit` ticks, then adds `arrivals_same_tick` — the sum of every in-flight allocation landing on exactly `tick + transit`. The docstring states the counting rule explicitly: *"every in-flight arrival up to and including t+k is counted (worst case for overflow)"* (`planning.py:122-123`).
2. **Arrivals from this same plan** — `constraints.py:108-109` sums `budget.added` for lines already committed this tick, filtered to `i.arrival_tick <= arrive`. The `Budget` dataclass is documented as *"Running totals while a plan is built, so line N sees the effect of lines 1..N-1"* (`constraints.py:80`), so line 7 in a plan is checked against lines 1–6.
3. **The safety margin** — `OVERFLOW_GUARD = 0.98` (`constraints.py:15`). The room returned is `0.98 × capacity − before`, i.e. the tank is filled to at most 98 % of capacity at arrival.
4. **The comparison** — `constraints.py:155-158` emits `Violation("OVERFLOW_AT_ARRIVAL", ...)` if `line.quantity > room + 1e-6`.

#### How pessimistic (p10) demand enters

The pessimism is inverted relative to intuition, and this is the subtle part. **Overflow occurs when demand turns out *lower* than expected** — a tank that would have drained on the p50 path stays full. So the pessimistic path for overflow is the **p10 (low) demand** series, not the high one.

- `constraints.py:106` calls `inventory_before_arrival(..., low=True)`.
- `planning.py:54-66` `PlanningState.demand(key, j, low)` selects `demand_p10` when `low=True`.
- `planning.py:100-101` documents it: *"`low=True` uses the p10 demand path: the pessimistic case for overflow (demand turns out lower)."*
- `planning.py:122-123` repeats it: *"Uses p10 demand by default because overflow happens when demand turns out low."*

The same asymmetry is honoured inside the LP: `optimizer.py:107` constrains overflow on `cum10` (the p10 cumulative demand) while the *unmet-demand* constraint `optimizer.py:104-105` uses `cum50` (the p50 path). The module docstring is explicit that this was a deliberate correction — the blueprint used p50 "and lost 222 L in the crisis run" (`optimizer.py:9-10`).

One further subtlety is folded into the projection: `PlanningState.demand` returns **0.0 for a station that is closed** at that tick (`planning.py:61-66`). A closed station accrues demand but its tank does not drain, so projecting normal consumption through an outage *overestimates* the room at arrival. The docstring records the measured cost of getting this wrong: *"measured: 80 L in the crisis scenario before this rule"* (`planning.py:59`).

#### The resulting guarantee

The guarantee is a **belt-and-braces two-layer** one, not a single check:

- **Layer 1 (LP-internal):** `optimizer.py:107` binds every station-fuel at every one of the 15 `CHECK` horizons so that `supply_in − cum10[m] ≤ cap × 0.98`. Because `supply_in` includes `known` in-flight arrivals plus the LP's own `x` and `y` variables (`optimizer.py:96-101`), overflow is impossible *by construction of the optimisation problem*.
- **Layer 2 (post-hoc re-validation):** `optimizer.py:173` calls `check_line(budget, line)` on every candidate line; if it returns violations, the line is dropped and recorded in `dropped` (`optimizer.py:174-176`). The module docstring states the design intent: *"every line is re-checked by constraints.check_line before it can be sent (the solver never has the last word)"* (`optimizer.py:11`).

Layer 2 is what makes "0 litres lost to overflow" a **guarantee rather than a lucky observation**, exactly as the journal argues in B2: *"Every policy and every manual edit goes through the same constraint check; that is what makes '0 litres destroyed' a guarantee rather than luck"* (`ENGINEERING_JOURNAL.md:126`). The heuristic path is protected identically (`heuristic.py:118`), and the manual/operator approval path is protected identically (`engine.py` re-validation on approval, per the journal B2 entry).

Finally, `room_now` is *also* enforced (`constraints.py:153-154`, `STATION_CAPACITY_NOW`) because the simulator itself will reject a submission whose `current inventory + quantity` exceeds capacity — so both the simulator's rule and physical reality are respected.

**Assessment.** The reasoning chain is complete and internally consistent. Puku CLI retrieved it, and the retrieval required cross-file synthesis across `planning.py`, `constraints.py`, `optimizer.py`, and `heuristic.py` — no single file contains the answer. The code comments themselves explain *why* each choice exists, which made verification unusually tractable.

---

### 2. Historical Debugging Analysis — The B1 idempotency-key collision

**File:** `docs/ENGINEERING_JOURNAL.md`, block **B1** (lines 72-101)

#### The bug

The journal is unusually blunt about the root cause (`ENGINEERING_JOURNAL.md:82-84`):

> **Simulation runs are now real database rows** (`db/repo.py: resume_or_create_sim_run`, `main.py: _on_run_start`).
> Before, the run id was an in-memory counter that restarted at 1 on every engine restart, so history writes failed their foreign key and, more dangerously, allocation idempotency keys (`fsip-{run}-{decision}-{line}`) would collide after a crash.

The failure chain:

1. `sim_run_id` was held in engine memory as a counter.
2. The engine crashed (or was `docker restart`ed). The counter reset to **1**.
3. The new run re-used `sim_run_id = 1`.
4. Idempotency keys are constructed as `fsip-{run}-{decision}-{line}` — `repo.py:265-267` — so a decision id of `1` in the *new* run produced the key `fsip-1-1-1`, which had **already been used** in the *first* run.
5. The simulator treats an idempotency key as a uniqueness constraint on a shipment. A key collision therefore means the simulator either **rejects the new allocation as a duplicate** (the shipment never happens — fuel is stuck at the depot) or, worse, is ambiguous about which allocation it refers to.

The deeper defect is that the identifier had **two levels of durability mismatch**: an in-memory counter scoped to a process lifetime was being used to namespace keys that must be unique across process lifetimes, and it was simultaneously used as a **foreign key** for history rows (`docs/ENGINEERING_JOURNAL.md:83`: *"history writes failed their foreign key"*).

The lesson the journal draws from B1 is methodological rather than technical (`ENGINEERING_JOURNAL.md:91-92`): *"Six of these bugs passed all unit tests because the tests never touched a real database or a real simulator."* The idempotency-collision class of bug is invisible to a unit test that stubs the repository — there is no state to be lost.

#### The fix

`sim_runs` became **real rows in TimescaleDB**, and the engine now performs a resume-or-create handshake on startup:

`services/engine/app/db/repo.py:53-90` — `resume_or_create_sim_run()`:

- **Resume path** (`repo.py:75-86`): look for the latest `SimRun` with `ended_at IS NULL` matching the same `scenario_id` and `seed`, ordered by `id DESC`. It is resumed only if its last persisted snapshot tick is `None` or `<= tick` — i.e. the simulator has not moved *ahead* of what we recorded.
- **Run-is-over path** (`repo.py:87-88`): if the simulator went backwards while the engine was down, the candidate run is closed via `close_sim_run()` and a new one is opened. The comment states the reason plainly: *"simulator went backwards while we were down: that run is over"*.
- **Reset path** (`repo.py:69-73`): on a simulator reset, the previous run is closed and a **new run row** is created — this is what makes a reset produce a *new* namespace rather than reusing the old one.
- **Create path** (`repo.py:89`): `create_sim_run()` commits a real row and returns it.

The docstring states the invariant the fix exists to protect (`repo.py:62-67`):

> *"After an engine restart the simulator world is unchanged, so we resume the latest open run of the same scenario/seed whose last persisted tick is not ahead of the simulator. This keeps decision ids and idempotency keys (`fsip-{run}-{decision}-{line}`) unique across restarts. On a simulator reset the previous run is closed and a new one is created."*

Because `sim_run_id` is now a **database sequence value**, it is monotonic and survives restarts, so `fsip-{run}-{decision}-{line}` is globally unique per (run, decision, line) for the lifetime of the database. Decision ids themselves are likewise database-generated, with a deliberate in-memory fallback that keeps the same key shape (`engine.py:217-219`): `repo.idempotency_key(self.store.sim_run_id, dec_id, i + 1)` with `dec_id = 10**9 + self._local_id` — negative line ids and a `10**9` offset so that locally-fabricated ids cannot collide with database-generated ones.

**Verification.** The journal records the evidence (`ENGINEERING_JOURNAL.md:99`): *"`sim_runs` resumed after `docker restart fsip-engine`"* — i.e. the behaviour was tested against the real container, not mocked. The chaos drill F11 independently confirms it at runtime: *"Engine killed (SIGKILL), restarted — state rebuilt from simulator + DB; duplicate idempotency keys after restart: 0"* (`docs/evidence/resilience.md:20`).

**Assessment.** This is a textbook *durability-boundary* bug, and the journal's framing — identifier scoped to the wrong lifetime — is the correct diagnosis. Puku CLI verified the fix against the actual implementation rather than the prose alone, and found that the implementation is **more careful than the prose**: the journal describes resume and reset, but the code also handles a third case the journal does not explicitly name (the simulator moving *backwards*), which `repo.py:87-88` closes out. That is a case where reading the code added information beyond the journal — exactly the value a code-level evaluation should demonstrate.

---

### 3. Algorithm & Constraint Verification — LP, not RL

**Verdict: Linear Programming (PuLP + CBC). There is no reinforcement learning anywhere in the decision path.**

#### Evidence for LP

`services/engine/app/decision/optimizer.py:1` — *"Rolling-horizon LP optimizer (PuLP 2.9 + CBC), weights tuned on the evaluation harness."*

The implementation confirms it structurally:

| Element | Location | Detail |
|---|---|---|
| Problem object | `optimizer.py:73` | `pulp.LpProblem("allocation", pulp.LpMinimize)` — a **minimisation** LP |
| Decision variables | `optimizer.py:74-84` | `x[rid, f]` (ship now) and `y[rid, f, j]` (ship in future slot `j`) — continuous, bounded |
| Objective | `optimizer.py:86-148` | Weighted linear sum: `W_UNMET=10`, `W_OVERFLOW=40`, `W_SAFETY=2`, `W_TERM=0.5`, `W_DEPOT_CLIP=20`, `W_TRANSIT=0.002`, `W_FAIR=5` (`optimizer.py:27`) |
| Constraints | `optimizer.py:105-147` | All linear: `inv >= 0` (no unmet demand), `supply_in − cum10 − o <= cap × 0.98` (overflow), `shipped <= depot_inventory − reserve + supply` (depot stock), `shipped <= dispatch_cap` (per-depot per-tick cap), `inv >= z × demand` (max-min fairness) |
| Solver | `optimizer.py:149` | `prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit))` — **CBC**, with a 5-second default limit (`optimizer.py:65`) |
| Optimality gate | `optimizer.py:150-151` | `if pulp.LpStatus[status] != "Optimal": raise OptimizerFailed(...)` — a non-optimal answer is a **failure**, not a best-effort answer |

The architecture is **model-predictive control** (`optimizer.py:2-6`): plan over a 96-tick horizon (`CHECK[-1] = 96`, `optimizer.py:25`), execute only the first tick, and re-plan every tick with fresh data. The `y` variables represent future dispatch slots so the solver can trade across stations rather than serving them one at a time.

#### Why not RL — and the measured reasoning

The journal answers this directly (`ENGINEERING_JOURNAL.md:425-429`):

> *"We measured the ceiling first. The demand process is `profile × multiplier × bounded noise`, which puts the best achievable forecast error at roughly 4.5-5.8 % WAPE. Our structural model reaches 5.8 %. A heavier model is competing for the last fraction of a percent while costing explainability and determinism, both of which this domain needs more."*

Three properties of this problem make RL a poor fit, and the codebase demonstrates all three:
1. **The world is deterministic.** Simulator finding #12 (`ENGINEERING_JOURNAL.md:375`): the reference benchmark reproduced exactly (crisis, 288 ticks, SL 0.991). RL gains nothing when the transition function is fixed and known.
2. **Demand is exogenous.** Finding #11 (`ENGINEERING_JOURNAL.md:374`): demand is a function of `(seed, tick, station, fuel)` and event multipliers — it does not depend on inventory. There is no learned dynamics to model.
3. **The constraints are coupled and linear.** Shared dispatch caps, shared depot stock, coupled station capacities. An LP solves these exactly; an RL agent would need many samples to rediscover what a solver returns in milliseconds.

The journal also records the **observable difference** between LP and greedy, which is the honest way to prove the optimizer earns its place (`ENGINEERING_JOURNAL.md:414-419`): on the 6-day baseline both reach the same total service level, but the optimiser lifts the worst-served station from **0.868 → 0.9215** — *"that is the LP trading across stations in a way a greedy rule cannot."* The B5 entry adds the fairness result: *"optimizer lifts the worst station 0.868 → 0.929 (baseline) and 0.714 → 0.737 (crisis) at equal total SL, i.e. it spreads scarcity more fairly"* (`ENGINEERING_JOURNAL.md:208`).

#### The heuristic fallback mechanism

The fallback is a **first-class, instrumented, and safe** mechanism — not an `except: pass`.

**Trigger conditions** (`optimizer.py:67-68`, `optimizer.py:150-151`):
- `OPTIMIZER_FORCE_FAIL=true` environment variable — a dedicated chaos drill that makes the optimizer fail deterministically.
- Any non-`Optimal` solver status — infeasible, unbounded, or **time-limit reached without optimality**.
- Any exception raised inside `optimizer.plan` (including `check_line` failures surfacing as exceptions).

**The fallback itself** (`decision/engine.py:140-151`):

```python
policy_used, fallback_reason = "heuristic", None
plan = None
if self.store.policy == "optimizer":
    try:
        plan = await asyncio.to_thread(optimizer.plan, state, self.reserve_fraction)
        policy_used = "optimizer"
    except Exception as err:  # solver error, timeout, infeasible, or the OPTIMIZER_FORCE_FAIL drill
        fallback_reason = f"optimizer failed ({str(err)[:80]}); heuristic used"
        FALLBACK_ACTIVATIONS.labels(component="optimizer", reason=type(err).__name__).inc()
        log.warning("fallback.activated", component="optimizer", reason=str(err)[:200])
if plan is None:
    plan = heuristic.plan(state, reserve_fraction=self.reserve_fraction)
```

Five properties make this well-designed:

1. **The solver never blocks the tick loop.** `asyncio.to_thread` (`engine.py:144`) moves the solve off the event loop, so a 5-second CBC solve cannot stall state ingestion. `DECISION_LATENCY.labels(policy=policy_used)` observes the real duration (`engine.py:152`), and the journal records the measured p95: *"Decide p95 72-205 ms"* (`ENGINEERING_JOURNAL.md:208`).
2. **The fallback is not a downgrade in safety.** The heuristic path calls the *same* `check_line` on every line (`heuristic.py:118`). The journal's phrasing is the correct one: *"the optimizer can never be less safe than the heuristic"* (`ENGINEERING_JOURNAL.md:206`). The optimizer's own docstring makes the same promise — *"the solver never has the last word"* (`optimizer.py:11`).
3. **The degradation is visible, not silent.** `fallback_reason` is stored on the decision record (`engine.py:186`), the console renders it as *"fallback policy"* (`ENGINEERING_JOURNAL.md:206`), and a `FALLBACK_ACTIVATIONS` Prometheus counter increments with a `reason` label.
4. **The degraded plan reaches a human, not a truck.** Confidence is derived independently of which policy ran (`engine.py:158-159`): `0.5` if data is stale/degraded, `0.6` if the forecast is on fallback, `0.9` otherwise. The autopilot gate is confidence ≥ 0.7, so a fallback *forecast* already forces human review. The journal states the fallback contract as a whole (`ENGINEERING_JOURNAL.md:431-433`): *"It falls back to the heuristic, marks the decision `fallback_reason`, lowers confidence, and the console says so. Autopilot will not execute below the confidence gate, so a degraded plan reaches a human instead of a truck."*
5. **The heuristic itself is a real policy, not a stub.** `heuristic.py` implements Monte-Carlo-aware risk ranking (`risk_level`, `heuristic.py:43-58`), fill-based reorder triggers with in-flight awareness (`heuristic.py:61-77`), shortest-safe-route selection, and a 90 %-on-p50 / 98 %-on-p10 fill target (`heuristic.py:104-108`). Measured: *"greedy, real simulator: service level 1.000 on the 3-day baseline, 0.992 in the combined crisis"* (`heuristic.py:3`).

**Assessment.** The verification was decisive and cheap: `import pulp`, `pulp.LpProblem`, `pulp.LpVariable`, `pulp.PULP_CBC_CMD` are unambiguous signatures of a linear program, and the absence of any policy-gradient / Q-learning / replay-buffer artefact anywhere in `decision/` corroborates the journal's claim. The fallback is one of the better-engineered degradation paths I have seen in a decision engine: instrumented, non-blocking, no safety regression, and it refuses to let a degraded plan reach an autonomous actor.

---

### 4. Resilience & SRE Assessment — The 11/11 chaos drills

**File:** `docs/evidence/resilience.md` (generated by `scripts/chaos/run_all.sh`; run started **2026-11-23T22:11:44Z** against the live stack).

#### Result: 11 / 11 drills passed

| # | Failure injected | Detected (s) | Mitigation (measured) | Recovered (s) | Result |
|---|---|---:|---|---:|---|
| F1 | Simulator unavailable (503), 75 s | 2.8 | Breaker OPEN; cached state served (HTTP 200, 63.93 s old); SAFE_HOLD after 61.4 s; no writes | 25.2 | PASS |
| F2 | Simulator error rate 0.5, 60 s | 9.1 | Retries + last-good per resource; ticks 588→594; failed allocations 0→0 | 17.6 | PASS |
| F3 | Simulator latency 2500 ms, 45 s | 6.9 | 2 s timeout counts as failure; nothing written on old data (p95 2002 ms) | 22.9 | PASS |
| F4 | Simulator `stale_data`, 45 s | 2.7 | `state.stale=true`, engine DEGRADED: autopilot paused, approvals 409 `STALE_DATA` | 0.0 | PASS |
| F5 | SSE refused 60 s (+ forced reconnect) | 2.2 | Polling fallback kept ticks moving 678→683; reconnect with backoff | 1.1 | PASS |
| F6 | Malformed `/v1/stations` (`wrong_type`) | 1.1 | Payload rejected, last-good kept, quarantine rows 32→48; error names the field | 2.6 | PASS |
| F7 | Prediction service stopped, 40 s | 3.8 | Forecast `source=fallback` (seasonal fallback); confidence 0.6 so a human reviews | 11.1 | PASS |
| F9 | Database stopped, 30 s | 4.3 | Engine kept running (tick 760→773); 32 writes buffered, flushed after restart | 4.3 | PASS |
| F10 | Redis stopped, 30 s | 2.3 | API served live state from the engine (HTTP 200, tick 780→795) | 3.2 | PASS |
| F11 | Engine killed (SIGKILL), restarted | 2.0 | State rebuilt from simulator + DB; duplicate idempotency keys after restart: **0** | 2.8 | PASS |
| F15 | Simulator reset | 0.6 | New `sim_run` #55 → #56; live state cleared, history kept | 0.0 | PASS |

#### Detection: 0.6 s – 9.1 s

Every drill is measured **from `/api/system/status`** — *"what the operator sees"* (`resilience.md:3-5`) — not from an internal metric. Detection ranges from **0.6 s** (F15, simulator reset) to **9.1 s** (F2, 50 % error rate), with a median of roughly **2.8 s**. The 9.1 s outlier on F2 is the honest one: a 50 % error rate is a *partial* fault, and the platform's breaker needed to accumulate enough evidence before opening. The journal documents the fix that produced the current number — the breaker previously counted every retry attempt as a failure, so at 50 % error rates it opened the breaker and froze the engine for 15 s even though half the calls worked; after the fix *"one failure per request after its retries (≈ 6 % of requests fail at 50 % attempt errors); ticks keep full pace (498 → 505 in 15 s)"* (`ENGINEERING_JOURNAL.md:236`, B7 problem #7).

Critically, health is judged from **real traffic** — breaker state, error ratio and p95 over the last 30 s, and the stale header — never from the simulator's `/v1/health`, which *"bypasses fault injection and answers 'ok' while every real call fails"* (journal finding #1, `ENGINEERING_JOURNAL.md:364`). This is the single most valuable design decision in the resilience layer, and the drills are its proof.

Two further detection properties are worth calling out:
- **F5** proves SSE is treated as a *trigger*, not a truth source. The drill had to *force a reconnect* — because `stream_disconnect` only refuses new SSE connections while an established stream keeps flowing (journal finding #10, `ENGINEERING_JOURNAL.md:373`). Ticks kept moving 678→683 via the polling fallback.
- **F6** proves the data-quality path is real: the malformed payload was rejected, the last-good state was kept, 16→48 quarantine rows accumulated, and the operator-facing message named the exact field (`invalid stations: 0.inventory: Value error, must be an object keyed by fuel type`).

#### Recovery: 0.0 s – 25.2 s, with no human action

Recovery is measured from **fault expiry** for drills F1–F6 (as the `Note` column states explicitly) and from **container start** for F7, F9, F10, F11 — the correct baseline in each case, since the fault cannot end before the thing that was stopped is running again. Range: **0.0 s** (F4 `stale_data` clears the instant the fault clears; F15 reset needs no recovery) to **25.2 s** (F1, the 75 s simulator outage, which includes the SAFE_HOLD transition at 61.4 s and the return to normal service).

The recovery claim is unusually well-qualified, which is what makes it trustworthy. Every drill's recovery criterion is *"recovered with no human action"* (`resilience.md:5`) — the remediation is automatic and unattended.

#### The drill that caught the drill

The most important line in this document is not in the table. The journal records that a previous version of the resilience suite was **reporting false recoveries** (`ENGINEERING_JOURNAL.md:456-459`):

> *"Because a drill once lied to us. A sticky `demand_fetch_failed` flag left autopilot silently disabled after every outage while health said NORMAL and every drill reported 'recovered'. We fixed the flag, added a regression test, and tightened the drill criterion so recovery now requires the decision inputs to be trusted again, not just the HTTP checks to pass."*

The underlying bug (journal B7 problem #2) was that `demand_fetch_failed` was set during the outage and **never cleared**, so every subsequent decision had confidence 0.5 — below the 0.7 autopilot gate — and the autopilot never executed again, while the health page cheerfully reported NORMAL. The fix (B7 problem #1) also reversed an SLO-policy error: the textbook *"budget < 25 % → autopilot off"* rule was measured making a 75 s outage *worse* (service level 99 % → 81 %), and was replaced with a **change freeze** — policy switches need an explicit override, chaos injects need a supervisor, operators get paged, and the remedy keeps running (`ENGINEERING_JOURNAL.md:230`).

This is the strongest evidence in the whole repository that the resilience numbers are measurements rather than claims: **the measuring instrument itself was found to be broken and was repaired, and the repair was itself drilled.**

#### Assessment

| Metric | Value |
|---|---|
| Drills passed | **11 / 11** |
| Detection window | **0.6 – 9.1 s** (median ≈ 2.8 s) |
| Recovery window | **0.0 – 25.2 s**, unattended |
| Human action required | **None** in any drill |
| Health source | Real traffic only, never the dependency's own health endpoint |
| Incident visibility | `GET /api/incidents` + System Health page, with detect/mitigate/recover timestamps |
| Independent corroboration | F11's "duplicate idempotency keys after restart: 0" confirms the B1 fix at runtime |

The platform's own summary (`ENGINEERING_JOURNAL.md:451-453`) states the same numbers: *"11 of 11 drills pass with detection in 0.6-9 s and unattended recovery in 0-25 s."* Puku CLI's independent reading of `resilience.md` reproduces those ranges exactly — **which is itself the cross-check that the evidence file and the journal are consistent.**

---

## Strengths Observed

**1. `PUKU.md` auto-discovery — the single highest-leverage feature.**
The repository's `PUKU.md` was located and injected into context automatically at session start, before any tool call. It carries the project identity (FSIP, BUP CSE FEST 2026), the five core architectural claims (Engine LP + heuristic, seasonal forecaster + Monte Carlo, stateless FastAPI, React console, TimescaleDB, Redis), the 11/11 resilience and 0-overflow invariants, the exact run commands (`bash scripts/smoke_test.sh`, `make test`, `make chaos`, `make k8s-up && make k8s-demo`, `bash scripts/clean_clone_test.sh`), and the engineering rules (branches cut from `dev`, `--no-ff` merges, **strict LF line endings** via `.gitattributes`, secrets in gitignored `.env`). That is an unusually high-signal onboarding document, and it made the first tool call an informed one rather than an exploratory one.

**2. Fast context indexing across a large, polyglot repository.**
FSIP spans Python services, a React/TypeScript frontend, a Helm chart, shell scripts, Prometheus rules and generated evidence. The evaluation required locating four semantically distinct artifacts — a constraint module, a historical narrative, an optimiser, and a generated resilience report — across that surface, and the CLI surfaced each on the first attempt with correct line-level accuracy. There was no re-searching, no directory listing loop, and no index-staleness artefact.

**3. Terminal ergonomics.**
The tool operates natively in the working directory with file-aware reads, edits, and glob/grep rather than shelling out blind. The grep results above came back with line numbers and three lines of context (`-C 3`), which is what made it practical to confirm the fallback logic in `engine.py:140-154` in a single call and to confirm the `idempotency_key` format at `repo.py:265-267` without opening the file a second time.

**4. Cross-file synthesis rather than single-file summarisation.**
None of the four benchmarks had an answer that lives in one file. Benchmark 1 required joining `planning.py` (projection maths, `low=True` semantics) → `constraints.py` (budget accounting, in-flight summation, the 0.98 guard) → `optimizer.py` (the LP-level overflow constraint on `cum10`) → `heuristic.py` (the quantity clamp). Benchmark 2 required joining the journal's prose → `repo.py:53-90` → `repo.py:265-267` → `engine.py:217-219`. The CLI assembled these chains without being asked to.

**5. Respect for the repository's own epistemic discipline.**
The codebase has an unusual and admirable habit of documenting *why* each number exists — the comment above `OVERFLOW_GUARD = 0.98` cites the lost-222 L crisis; the `_closed()` docstring cites the measured 80 L; the breaker comment cites the 15 s freeze. This meant the CLI could verify *intent* rather than merely transcribe *behaviour*, and could spot cases where the code is more careful than the prose (the "simulator went backwards" branch at `repo.py:87-88`, which the B1 journal entry does not name).

**6. Evidence discipline — following the artefact, not the claim.**
The journal's own standard is *"Evidence over claims. Every capability we present has a test, a drill script or a report in `docs/evidence/`"* (`ENGINEERING_JOURNAL.md:51`). The CLI's evaluation naturally inherits that standard: benchmark 4 was answerable directly from a generated report whose provenance line reads *"generated by `scripts/chaos/run_all.sh`"* — a claim that is falsifiable, and whose numbers agree with the journal's independent summary.

**7. Correct handling of a subtle domain inversion.**
The most valuable single finding was the p10/p50 asymmetry for overflow: **overflow is the risk of demand being too *low***, so the pessimistic path for overflow is p10 while the pessimistic path for unmet demand is p50. This is stated in three separate places (`planning.py:100-101`, `planning.py:122-123`, `optimizer.py:106`) and implemented consistently. Getting this backwards would have silently inverted the safety margin.

---

## Constructive Feedback & Optimization Suggestions for the Poridhi Engineering Team

**1. Add a machine-readable "operational surface" section to `PUKU.md`.**
`PUKU.md` documents *how to build and test* the project well, but not *how to operate* it. A short block listing the chaos-drill entry point (`scripts/chaos/run_all.sh`), which evidence file each drill regenerates, the container names (`fsip-engine`, `fsip-api`), and the read-only diagnostic endpoints (`/api/system/status`, `/api/incidents`, `/api/decisions`) would let the CLI answer operational questions without a repository search. This is the single change that would most improve the SRE-usability score above 4.

**2. Introduce a `docs/evidence/index.md` with provenance columns.**
`docs/evidence/` currently holds 12 artefacts with no index. A table of `artefact → generated by → last run → what it proves` would let the CLI (and a judge) establish which numbers are current without opening each file. It also addresses a real risk the journal itself demonstrates: the B6 entry quotes different resilience numbers (1.6 s detection, 60.0 s SAFE_HOLD, 8.4 s recovery) than the current `resilience.md` (2.8 s, 61.4 s, 25.2 s), because the file was regenerated. An index with a run timestamp makes that drift explicit instead of leaving two sets of numbers in the narrative.

**3. Distinguish "generated" from "curated" evidence in file headers.**
`resilience.md` states its generator, which is excellent. Applying the same convention to `evaluation.md`, `observability.md`, `contingency.md`, `fairness.md` and `loadtest-report.md` would let the CLI attribute every number to a specific run without inspecting content, and would let it flag stale artefacts during an evaluation.

**4. Give the CLI a first-class "evidence" tool.**
The strongest analytical moves in this evaluation were cross-file: the LP overflow constraint vs. the constraint-module check; the journal prose vs. the `repo.py` implementation; the resilience table vs. the journal's summary. A tool that queries "which numbers in this report appear in the codebase, and where" — or that resolves a `docs/evidence/*.md` reference to its generator — would automate exactly the verification that consumed most of the effort here.

**5. Encode the invariant catalogue as structured data.**
The "Key Invariant" line in `PUKU.md` — *0 failed shipments, 0 litres lost to overflow* — is the most important sentence in the repository, and it is prose. A machine-readable `invariants.json` (id, statement, enforcing module, enforcement mechanism, CI gate, evidence link) would let the CLI verify an invariant's *enforcement point* rather than its assertion, and would give the CI decision-quality gate a single source of truth. Benchmark 1 and benchmark 3 would then both become one lookup.

**6. Teach the CLI the repo's adversarial-failure vocabulary.**
This codebase has a specific failure vocabulary that is not inferable from names: `stale`, `degraded`, `SAFE_HOLD`, `NO_FEASIBLE_ROUTES`, breaker state, `fsip_fallback_activations_total`, `fsip_engine_mode`, `state.stale`, quarantine rows, break-glass. A short glossary in `PUKU.md` — or a `docs/glossary.md` — would let the CLI interpret operational output correctly on the first pass instead of reasoning it out from code.

**7. Add "what this tool must never do" guidance.**
The journal's sharpest engineering lessons are prohibitions: never trust a health endpoint you did not measure; never let a solver have the last word; never let a degraded plan reach an autopilot; never ship an explanation containing an ungrounded number. `PUKU.md` states the rules of the branch and the LF line endings, but not the *epistemic* rules. Adding them would make the CLI's default suggestions align with the project's actual culture rather than merely its conventions.

**8. Leverage the gap-analysis opportunity between `PUKU.md` and the repository.**
`PUKU.md` describes "Engine (LP + Heuristic), Intelligence (Seasonal forecaster + Monte Carlo)" and a "React Operator Console" — accurate but high-level. The most valuable context is one level deeper: the *fallback contract* (optimizer → heuristic → confidence 0.6 → human), the *single-writer invariant*, the *p10/p50 asymmetry*, and the *break-glass static console*. These are the four facts a new agent or new engineer most needs and most often gets wrong. Surfacing them in `PUKU.md` would compress the first hour of onboarding into the first minute.

**9. Keep the human-verification gate explicit in the tool's own workflow.**
The project's stated method is *"AI-assisted, human-verified ... Measured numbers in this journal come from real runs, not from the AI"* (`ENGINEERING_JOURNAL.md:48-50`). Puku CLI's behaviour during this evaluation matched that contract well: it read generated evidence rather than re-deriving claims, cited file-and-line for every assertion, and did not present a single number it could not trace to a source. Adding a `--verify` mode that mechanically re-checks the citations in a generated document against the repository before the file is written would make that guarantee mechanical rather than behavioural.

---

## Summary

Poridhi Puku CLI was evaluated on four benchmarks spanning deep code comprehension, historical debugging analysis, algorithm verification, and SRE assessment against a live, evidence-backed resilience platform. It retrieved every target artefact on the first attempt, produced file-and-line citations for every claim, correctly identified the non-obvious p10/p50 overflow asymmetry, distinguished the LP from a hypothetical RL approach on structural evidence rather than on the journal's word, verified the B1 idempotency fix against the implementation (finding a code path the journal does not mention), and reproduced the resilience figures — including the `Note`-column caveat about how recovery is counted — without distortion.

It did not hallucinate, did not over-claim, and did not silently paper over the one discrepancy it found (the B6 journal figures vs. the regenerated `resilience.md`). Its weakness is narrow and specific: it has no native model of *this* platform's operational surface, so it can read the resilience evidence expertly but cannot, unprompted, tell you which script regenerates it. That is a documentation and tooling gap rather than a reasoning gap, and the eight suggestions above are the concrete path to closing it.

**Score: 19 / 20.**
