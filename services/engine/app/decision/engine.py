"""Decision engine: plan → record → (approve | autopilot) → execute → track outcomes.

Design choices (logged in docs/assumptions.md):
- One open recommendation per (station, fuel). While a recommendation waits for a human, the engine does not plan the
  same need again (otherwise an operator could approve two plans for the same gap and overflow the station).
- Recommendations live DECISION_TTL_TICKS (default 8 = 2 sim hours) instead of 2 ticks: at demo speed 0.5 tick/s two
  ticks are 4 seconds, too short for a human. Every line is re-validated against fresh data at approval time and
  trimmed (or dropped) if the world moved.
- Autopilot executes only when the gate passes (20 §5): confidence ≥ 0.7, every line ≤ 7,000 L, data not stale,
  engine NORMAL. Otherwise the decision waits for a human, even in autopilot.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any

from fsip_common.logging import get_logger
from prometheus_client import Counter, Gauge, Histogram

from ..db import repo
from ..intelligence.client import FALLBACK_ACTIVATIONS
from ..risk.stockout import shipment_impact
from ..sim.client import SimClient
from ..state.publisher import Publisher
from ..state.store import WorldState
from . import heuristic, optimizer
from .constraints import MIN_LINE_LITERS, Budget, Line, check_line
from .executor import decision_status_after_execution, submit_line
from .planning import PlanningState, inflight_from_allocations, naive_forecast

log = get_logger()

DECISIONS_TOTAL = Counter("fsip_decisions_total", "Decisions created", ["policy", "status"])
DECISION_LATENCY = Histogram("fsip_decision_plan_seconds", "Time to build a plan", ["policy"],
                             buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5))
ALLOCATION_FAILURES = Counter("fsip_allocation_failures_total", "Our allocations that FAILED in the simulator")
PENDING_RECOMMENDATIONS = Gauge("fsip_pending_recommendations", "Recommendations waiting for a human")
OPERATOR_ACTIONS = Counter("fsip_operator_actions_total", "Operator actions on decisions", ["action"])

OPEN_LINE_STATES = {"SUBMITTED", "PENDING", "IN_TRANSIT"}
TRIMMABLE = {"OVERFLOW_AT_ARRIVAL", "STATION_CAPACITY_NOW", "DEPOT_INVENTORY", "DISPATCH_CAPACITY",
             "ROUTE_MAX_SHIPMENT"}
HORIZON = 96


class DecisionError(Exception):
    def __init__(self, status: int, code: str, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.data = status, code, message, data or {}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class DecisionEngine:
    def __init__(self, store: WorldState, client: SimClient, publisher: Publisher, sessionmaker_fn,
                 *, ttl_ticks: int = 8, interval_ticks: int = 1, auto_max_line: float = 7000.0,
                 auto_min_confidence: float = 0.7, reserve_fraction: float = 0.0, forecast_service=None) -> None:
        self.store = store
        self.client = client
        self.publisher = publisher
        self.sessionmaker_fn = sessionmaker_fn
        self.ttl_ticks = ttl_ticks
        self.interval_ticks = max(1, interval_ticks)
        self.auto_max_line = auto_max_line
        self.auto_min_confidence = auto_min_confidence
        self.reserve_fraction = reserve_fraction
        self.forecast_service = forecast_service
        self.last_plan_ms: float | None = None
        self.last_policy_used: str | None = None
        self.last_fallback_reason: str | None = None
        self.decisions: OrderedDict[int, dict[str, Any]] = OrderedDict()  # recent, newest last
        self.last_planned_tick = -1
        self.last_cycle_tick = -1
        self.force_plan = False
        self._local_id = int(time.time())  # only used when the DB is down
        self._lock = asyncio.Lock()
        self._run_id = store.sim_run_id

    # ------------------------------------------------------------------ planning view
    def planning_state(self) -> PlanningState:
        snap = self.store.snapshot
        if snap is None:
            raise DecisionError(503, "NO_STATE", "no simulator state yet")
        routes = {r.id: r for r in snap.routes}
        stations = {s.id: s for s in snap.stations}
        tick = snap.instance.tick
        fc = self.forecast_service.last if self.forecast_service is not None else None
        if fc is not None and fc.tick == tick:
            p50, p10, model, fallback = fc.p50, fc.p10, fc.model, fc.source == "fallback"
        else:
            history = {k: [o.demand_liters for o in v] for k, v in self.store.demand.items()}
            p50, p10 = naive_forecast(history, stations, HORIZON)
            model, fallback = "naive", True
        risk = {k: (v["level"], v["hours_to_stockout"]) for k, v in self.store.risk.items()}
        dispatched: dict[str, float] = {}
        for a in snap.allocations:
            if a.created_tick == tick and a.status == "PENDING":
                dispatched[a.source_depot_id] = dispatched.get(a.source_depot_id, 0.0) + a.quantity
        return PlanningState(
            tick=tick, tick_minutes=snap.instance.tick_minutes, stations=stations,
            depots={d.id: d for d in snap.depots}, routes=routes, regions={r.id: r for r in snap.regions},
            events=list(snap.events), supply=list(snap.supply_arrivals),
            inflight=inflight_from_allocations(snap.allocations, routes, tick),
            demand_p50=p50, demand_p10=p10, dispatched_this_tick=dispatched,
            stale=self.store.stale, degraded=self.store.degraded, forecast_model=model,
            forecast_fallback=fallback, risk=risk,
        )

    # ------------------------------------------------------------------ cycle hook
    async def on_cycle(self, ws: WorldState) -> None:
        if ws.snapshot is None:
            return
        if ws.sim_run_id != self._run_id:  # simulator reset: old decisions belong to the old world
            self.decisions.clear()
            self._run_id = ws.sim_run_id
            self.last_planned_tick = -1
        tick = ws.snapshot.instance.tick
        async with self._lock:
            await self._track_outcomes(ws)
            await self._expire(tick)
            due = tick > self.last_planned_tick and (tick % self.interval_ticks == 0)
            if due or self.force_plan:
                self.force_plan = False
                await self._plan_and_maybe_execute(trigger="scheduled")
            self.last_cycle_tick = tick
        await self._publish()

    async def _plan_and_maybe_execute(self, trigger: str) -> dict[str, Any] | None:
        state = self.planning_state()
        self.last_planned_tick = state.tick
        covered = {(ln["destination_station_id"], ln["fuel_type"])
                   for d in self.decisions.values() if d["status"] == "PENDING_APPROVAL" for ln in d["lines"]}
        t0 = time.perf_counter()
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
        DECISION_LATENCY.labels(policy=policy_used).observe(time.perf_counter() - t0)
        self.last_plan_ms = round((time.perf_counter() - t0) * 1000, 1)
        self.last_policy_used, self.last_fallback_reason = policy_used, fallback_reason
        lines = [ln for ln in plan.lines if (ln.destination_station_id, ln.fuel_type) not in covered]
        if not lines:
            return None
        # 05 §4.4 simplified: fresh data + primary model 0.9; fallback forecast 0.6 (< autopilot gate → human review)
        confidence = 0.5 if (state.stale or state.degraded) else (0.6 if state.forecast_fallback else 0.9)
        decision = await self._record(state, lines, plan, policy_used, fallback_reason, confidence, trigger)
        if self._autopilot_gate(decision, state):
            await self._execute(decision, actor="autopilot")
        return decision

    def _autopilot_gate(self, decision: dict[str, Any], state: PlanningState) -> bool:
        return (self.store.operating_mode == "autopilot"
                and decision["confidence"] >= self.auto_min_confidence
                and all(ln["quantity"] <= self.auto_max_line for ln in decision["lines"])
                and not state.stale and not state.degraded
                and self.store.engine_mode == "NORMAL")

    # ------------------------------------------------------------------ records
    async def _record(self, state: PlanningState, lines: list[Line], plan: heuristic.Plan, policy: str,
                      fallback_reason: str | None, confidence: float, trigger: str) -> dict[str, Any]:
        total = sum(ln.quantity for ln in lines)
        summary = "; ".join(f"{ln.quantity:,.0f} L {ln.fuel_type} {ln.source_depot_id.removeprefix('depot-')}"
                            f"→{ln.destination_station_id.removeprefix('station-')}" for ln in lines)
        critical = any(ln.critical for ln in lines)
        drivers = [n.reason for n in plan.needs if (n.key[0], n.key[1]) in
                   {(ln.destination_station_id, ln.fuel_type) for ln in lines}]
        impact = self._impact(state, lines)
        decision: dict[str, Any] = {
            "sim_run_id": self.store.sim_run_id, "tick": state.tick, "policy": policy,
            "policy_version": plan.policy_version, "trigger": "alert" if critical else trigger,
            "status": "PENDING_APPROVAL", "mode": self.store.operating_mode, "confidence": confidence,
            "degraded": state.stale or state.degraded, "fallback_reason": fallback_reason,
            "expires_at_tick": state.tick + self.ttl_ticks, "total_liters": round(total, 1),
            "summary": f"Ship {summary}", "requires_second_approval": False, "approvals": [],
            "impact": impact, "narrative": None, "narrative_source": "template",
            "explanation": {
                "headline": drivers[0] if drivers else "Keep stations above their reorder level",
                "why_at_risk": [{"text": d} for d in drivers],
                "constraints": ["no departure on a route disrupted now or at departure",
                                "projected inventory at arrival (in-flight included) ≤ 98% capacity",
                                "per-depot dispatch cap shared by all fuels"],
                "unserved": plan.skipped,
                "confidence": confidence,
                "confidence_factors": {"fresh_data": 0.0 if (state.stale or state.degraded) else 1.0,
                                       "primary_forecast": 0.0 if state.forecast_fallback else 1.0},
                "policy": plan.policy_version, "forecast_model": state.forecast_model,
                "degraded": state.stale or state.degraded,
            },
            "created_at": _now_iso(),
        }
        line_rows = [{"source_depot_id": ln.source_depot_id, "destination_station_id": ln.destination_station_id,
                      "route_id": ln.route_id, "fuel_type": ln.fuel_type, "quantity": ln.quantity,
                      "status": "PLANNED", "reason": ln.reason} for ln in lines]
        db_fields = {k: decision[k] for k in ("sim_run_id", "tick", "policy", "policy_version", "trigger", "status",
                                               "mode", "confidence", "degraded", "fallback_reason",
                                               "expires_at_tick", "explanation")}
        try:
            async with self.sessionmaker_fn()() as session:
                dec_id, ids = await repo.create_decision_with_lines(session, db_fields, line_rows)
        except Exception as err:
            self._local_id += 1
            dec_id = 10**9 + self._local_id
            ids = [(-(i + 1), repo.idempotency_key(self.store.sim_run_id, dec_id, i + 1)) for i in range(len(lines))]
            decision["persisted"] = False
            log.error("decision.persist_failed", error=str(err), local_id=dec_id)
        decision["id"] = dec_id
        decision["lines"] = [{**row, "id": lid, "line_no": n, "idempotency_key": key, "sim_allocation_id": None,
                              "sim_error_code": None, "expected_arrival_tick": None, "actual_arrival_tick": None,
                              "received_liters": None}
                             for n, (row, (lid, key)) in enumerate(zip(line_rows, ids, strict=True), start=1)]
        self.decisions[dec_id] = decision
        while len(self.decisions) > 200:
            self.decisions.popitem(last=False)
        DECISIONS_TOTAL.labels(policy=policy, status="created").inc()
        log.info("decision.created", decision_id=dec_id, tick=state.tick, lines=len(lines), liters=round(total),
                 policy=plan.policy_version, mode=self.store.operating_mode)
        await self.publisher.event("decision.created", self._summary(decision))
        return decision

    def _impact(self, state: PlanningState, lines: list[Line]) -> dict[str, Any]:
        """Per target: the brief §9 alert card numbers, with the 12 h stockout probability before → after."""
        fc = self.forecast_service.last if self.forecast_service is not None else None
        p90 = fc.p90 if fc is not None else {}
        targets: dict[tuple[str, str], dict[str, Any]] = {}
        for ln in lines:
            key = (ln.destination_station_id, ln.fuel_type)
            t = targets.setdefault(key, {"lines": [], "qty": 0.0})
            t["lines"].append(ln)
            t["qty"] += ln.quantity
        out = []
        for key, t in targets.items():
            r = self.store.risk.get(key, {})
            arrival = state.tick + min(state.routes[ln.route_id].transit_ticks for ln in t["lines"])
            imp = shipment_impact(state, key, arrival, t["qty"], self.store.sim_run_id, p90)
            out.append({
                "station_id": key[0], "station_name": state.stations[key[0]].name, "fuel_type": key[1],
                "projected_stockout_hours": r.get("hours_to_stockout"),
                "current_inventory": r.get("inventory", float(state.stations[key[0]].inventory[key[1]])),
                "expected_demand_12h": r.get("expected_demand_12h"),
                "recommended": [{"liters": ln.quantity, "from": ln.source_depot_id, "route_id": ln.route_id}
                                for ln in t["lines"]],
                **imp, "arrival_tick": arrival,
            })
        network = {"unmet_before": sum(t["unmet_before"] for t in out),
                   "unmet_after": sum(t["unmet_after"] for t in out)}
        return {"targets": out, "network": network, "horizon_hours": 12,
                "method": "Monte Carlo, 300 demand paths, risk counted from the arrival tick"}

    async def _db(self, fn, *args, **kwargs) -> None:
        try:
            async with self.sessionmaker_fn()() as session:
                await fn(session, *args, **kwargs)
        except Exception as err:
            log.warning("decision.db_write_failed", fn=fn.__name__, error=str(err))

    async def _set_status(self, decision: dict[str, Any], status: str, **extra: Any) -> None:
        stamp_approval = bool(extra.pop("approved_at", False))
        decision["status"] = status
        decision.update(extra)
        if stamp_approval:
            decision["approved_at"] = _now_iso()
        if decision.get("persisted", True):
            db_extra = {k: v for k, v in extra.items() if k in ("approved_by", "rejected_reason")}
            if stamp_approval:
                db_extra["approved_at"] = datetime.now(UTC)
            await self._db(repo.update_decision, decision["id"], status=status, **db_extra)

    async def _set_line(self, line: dict[str, Any], **values: Any) -> None:
        line.update(values)
        if line["id"] > 0:
            await self._db(repo.update_line, line["id"], **values)

    # ------------------------------------------------------------------ execution
    async def _execute(self, decision: dict[str, Any], actor: str) -> None:
        statuses = []
        for line in decision["lines"]:
            if line["status"] != "PLANNED":
                statuses.append(line["status"])
                continue
            out = await submit_line(self.client, line)
            await self._set_line(line, status=out.status, sim_allocation_id=out.sim_allocation_id,
                                 sim_error_code=out.sim_error_code,
                                 expected_arrival_tick=out.expected_arrival_tick,
                                 **({"reason": f"{line['reason']} | {out.reason}"} if out.reason else {}))
            statuses.append(out.status)
        status = decision_status_after_execution(statuses)
        approvals = decision["approvals"] + [{"actor": actor, "role": "autopilot" if actor == "autopilot"
                                              else "operator", "at": _now_iso()}]
        await self._set_status(decision, status, approved_by=actor, approved_at=True, approvals=approvals)
        DECISIONS_TOTAL.labels(policy=decision["policy"], status=status).inc()
        log.info("decision.executed", decision_id=decision["id"], status=status, actor=actor)
        await self.publisher.event("decision.executed", self._summary(decision))

    async def _track_outcomes(self, ws: WorldState) -> None:
        by_key = {a.idempotency_key: a for a in ws.snapshot.allocations}
        for d in self.decisions.values():
            for line in d["lines"]:
                if line["status"] not in OPEN_LINE_STATES:
                    continue
                a = by_key.get(line["idempotency_key"])
                if a is None or a.status == line["status"]:
                    continue
                if a.status == "FAILED":
                    ALLOCATION_FAILURES.inc()
                    log.error("allocation.failed", key=a.idempotency_key, reason=a.failure_reason)
                await self._set_line(line, status=a.status, sim_allocation_id=a.id,
                                     expected_arrival_tick=a.expected_arrival_tick,
                                     actual_arrival_tick=a.actual_arrival_tick)
                if a.status == "ARRIVED":
                    log.info("allocation.arrived", key=a.idempotency_key, tick=a.actual_arrival_tick)

    async def _expire(self, tick: int) -> None:
        for d in list(self.decisions.values()):
            if d["status"] == "PENDING_APPROVAL" and tick > d["expires_at_tick"]:
                await self._set_status(d, "EXPIRED")
                log.info("decision.expired", decision_id=d["id"], tick=tick)
                await self.publisher.event("decision.expired", self._summary(d))

    # ------------------------------------------------------------------ operator API
    def get(self, decision_id: int) -> dict[str, Any]:
        d = self.decisions.get(decision_id)
        if d is None:
            raise DecisionError(404, "NOT_FOUND", f"decision {decision_id} not found")
        return d

    async def approve(self, decision_id: int, actor: str, line_ids: list[int] | None = None,
                      quantities: dict[str, float] | None = None) -> dict[str, Any]:
        async with self._lock:
            d = self.get(decision_id)
            if d["status"] == "EXPIRED":
                raise DecisionError(409, "RECOMMENDATION_EXPIRED", "recommendation expired; a new plan will follow")
            if d["status"] != "PENDING_APPROVAL":
                raise DecisionError(409, "INVALID_STATE", f"decision is {d['status']}")
            state = self.planning_state()
            if state.tick > d["expires_at_tick"]:
                await self._set_status(d, "EXPIRED")
                raise DecisionError(409, "RECOMMENDATION_EXPIRED", "recommendation expired; a new plan will follow")
            if state.stale:
                raise DecisionError(409, "STALE_DATA", "simulator data is stale; approval is blocked until it is fresh")
            await self._revalidate(d, state, line_ids, quantities or {})
            if not any(ln["status"] == "PLANNED" for ln in d["lines"]):
                raise DecisionError(422, "CONSTRAINT_VIOLATION", "no line is feasible any more",
                                    {"violations": [{"line_id": ln["id"], "message": ln["reason"]}
                                                    for ln in d["lines"]]})
            OPERATOR_ACTIONS.labels(action="approve").inc()
            await self._db(repo.audit_log, actor=actor, action="decision.approve", entity_type="decision",
                           entity_id=str(decision_id), payload={"line_ids": line_ids, "quantities": quantities})
            await self._execute(d, actor=actor)
        await self._publish()
        return d

    async def _revalidate(self, d: dict[str, Any], state: PlanningState, line_ids: list[int] | None,
                          quantities: dict[str, float]) -> None:
        """Re-check every line against fresh data; trim capacity-type violations, drop the rest."""
        budget = Budget(state, reserve_fraction=self.reserve_fraction)
        for ln in d["lines"]:
            if line_ids is not None and ln["id"] not in line_ids:
                await self._set_line(ln, status="CANCELLED", reason=f"{ln['reason']} | excluded by operator")
                continue
            qty = float(quantities.get(str(ln["id"]), ln["quantity"]))
            line = Line(ln["route_id"], ln["source_depot_id"], ln["destination_station_id"], ln["fuel_type"], qty,
                        ln["reason"])
            violations = check_line(budget, line)
            if violations and all(v.rule in TRIMMABLE for v in violations):
                limit = min(state.routes[line.route_id].max_shipment,
                            budget.depot_left.get((line.source_depot_id, line.fuel_type), 0.0),
                            budget.dispatch_left(line.source_depot_id),
                            budget.room_at_arrival(line.route_id, line.destination_station_id, line.fuel_type),
                            budget.room_now(line.destination_station_id, line.fuel_type))
                trimmed = float(int(max(0.0, limit) // 10) * 10)
                if trimmed >= MIN_LINE_LITERS:
                    line = Line(line.route_id, line.source_depot_id, line.destination_station_id, line.fuel_type,
                                trimmed, line.reason)
                    violations = check_line(budget, line)
            if violations:
                await self._set_line(ln, status="CANCELLED",
                                     reason=f"{ln['reason']} | dropped at approval: {violations[0].message}")
                continue
            if line.quantity != ln["quantity"]:
                await self._set_line(ln, quantity=line.quantity,
                                     reason=f"{ln['reason']} | adjusted {ln['quantity']:,.0f}→{line.quantity:,.0f} L "
                                            f"at approval (fresh data)")
            budget.commit(line)

    async def reject(self, decision_id: int, actor: str, reason_code: str, comment: str = "") -> dict[str, Any]:
        async with self._lock:
            d = self.get(decision_id)
            if d["status"] != "PENDING_APPROVAL":
                raise DecisionError(409, "INVALID_STATE", f"decision is {d['status']}")
            await self._set_status(d, "REJECTED", rejected_reason=f"{reason_code}: {comment}".strip(": "))
            OPERATOR_ACTIONS.labels(action="reject").inc()
            await self._db(repo.audit_log, actor=actor, action="decision.reject", entity_type="decision",
                           entity_id=str(decision_id), payload={"reason_code": reason_code, "comment": comment})
            await self.publisher.event("decision.rejected", self._summary(d))
        await self._publish()
        return d

    async def plan_now(self, actor: str) -> dict[str, Any]:
        async with self._lock:
            decision = await self._plan_and_maybe_execute(trigger="manual")
        await self._db(repo.audit_log, actor=actor, action="decision.plan_now", entity_type="engine", entity_id="-")
        await self._publish()
        return {"accepted": True, "decision_id": decision["id"] if decision else None,
                "tick": self.last_planned_tick}

    async def set_mode(self, actor: str, mode: str) -> dict[str, Any]:
        if mode not in ("advisory", "autopilot"):
            raise DecisionError(422, "BAD_MODE", "mode must be advisory or autopilot")
        if mode == "autopilot" and (self.store.stale or self.store.degraded):
            raise DecisionError(409, "STALE_DATA", "cannot enable autopilot while data is stale or degraded")
        self.store.operating_mode = mode
        OPERATOR_ACTIONS.labels(action=f"mode_{mode}").inc()
        await self._db(repo.audit_log, actor=actor, action="engine.mode", entity_type="engine", entity_id="mode",
                       payload={"mode": mode})
        await self.publisher.event("mode.changed", {"engine_mode": self.store.engine_mode, "operating_mode": mode,
                                                    "reasons": sorted(self.store.degraded_reasons)})
        return {"mode": mode}

    async def set_policy(self, actor: str, policy: str) -> dict[str, Any]:
        if policy not in ("optimizer", "heuristic"):
            raise DecisionError(422, "BAD_POLICY", "policy must be optimizer or heuristic")
        self.store.policy = policy
        await self._db(repo.audit_log, actor=actor, action="engine.policy", entity_type="engine",
                       entity_id="policy", payload={"policy": policy})
        return {"policy": policy}

    # ------------------------------------------------------------------ publishing
    @staticmethod
    def _summary(d: dict[str, Any]) -> dict[str, Any]:
        return {k: d.get(k) for k in ("id", "tick", "status", "policy", "policy_version", "total_liters", "summary",
                                       "confidence", "mode", "expires_at_tick")}

    async def _publish(self) -> None:
        items = list(reversed(self.decisions.values()))
        pending = [d for d in items if d["status"] == "PENDING_APPROVAL"]
        PENDING_RECOMMENDATIONS.set(len(pending))
        self.store.pending_recommendations = [self._summary(d) for d in pending]
        await self.publisher.set_json("fsip:decisions", items[:100])

