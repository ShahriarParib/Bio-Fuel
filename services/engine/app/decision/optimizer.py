"""Rolling-horizon LP optimizer (05 §4.3, tuned v4 from reference/policy_benchmark.py; PuLP 2.9 + CBC).

Model-predictive control: plan shipments for now (x) and for future dispatch slots (y) over 96 ticks, execute only x,
re-plan every tick with fresh data. Why an LP: the constraints are coupled (one dispatch cap shared by every fuel and
route of a depot, depot stock shared by stations, scheduled supply that must fit, routes cut in the future), and an
exact solver trades them off instead of serving stations one at a time like the greedy policy.

Changes vs the benchmark blueprint:
- overflow is constrained on the LOW (p10) demand path: overflow happens when demand turns out lower than expected
  (the blueprint used p50 and lost 222 L in the crisis run);
- every line is re-checked by constraints.check_line before it can be sent (the solver never has the last word);
- any non-optimal status, exception or OPTIMIZER_FORCE_FAIL → the caller falls back to the heuristic.
"""
from __future__ import annotations

import os

import pulp

from .constraints import MIN_LINE_LITERS, Budget, Line, check_line, dispatch_cap, route_blocked_for_departure
from .heuristic import Plan, assess_needs
from .planning import FUELS, Key, PlanningState

POLICY_VERSION = "optimizer@1.0.0"
CHECK = [2, 3, 4, 6, 8, 12, 16, 20, 24, 32, 40, 48, 64, 80, 96]
SLOTS = [4, 8, 12, 16, 20, 28, 36, 48, 64]
W_UNMET, W_OVERFLOW, W_SAFETY, W_TERM, W_DEPOT_CLIP, W_TRANSIT, W_FAIR = 10, 40, 2, 0.5, 20, 0.002, 5
SAFETY_TICKS, SAFETY_NOISE, TERM_FILL, OVERFLOW_GUARD = 16, 0.15, 0.9, 0.98


class OptimizerFailed(Exception):
    pass


def _blocked_at(state: PlanningState, route_id: str, tick: int) -> bool:
    for e in state.events:
        if e.type != "route_disruption" or e.status == "RESOLVED":
            continue
        ids = e.parameters.get("route_ids") or []
        if (not ids or route_id in ids) and e.start_tick <= tick < e.end_tick:
            return True
    return False


def _outage_at(state: PlanningState, sid: str, tick: int) -> bool:
    for e in state.events:
        if e.type != "station_outage" or e.status == "RESOLVED":
            continue
        ids = e.parameters.get("station_ids") or []
        if (not ids or sid in ids) and e.start_tick <= tick < e.end_tick:
            return True
    return state.stations[sid].status == "OUTAGE" and tick == state.tick


def _cumulative(state: PlanningState, key: Key, horizon: int, low: bool) -> list[float]:
    acc, out = 0.0, [0.0]
    for k in range(horizon):
        if not _outage_at(state, key[0], state.tick + k):
            acc += state.demand(key, k, low=low)
        out.append(acc)
    return out


def solve(state: PlanningState, reserve_fraction: float = 0.0, fair: bool = True,
          time_limit: float = 5.0) -> dict[tuple[str, str], float]:
    """Return liters to ship NOW per (route, fuel). Raises OptimizerFailed if no optimal solution."""
    if os.getenv("OPTIMIZER_FORCE_FAIL", "false").lower() == "true":
        raise OptimizerFailed("OPTIMIZER_FORCE_FAIL drill")
    t, horizon = state.tick, CHECK[-1]
    st, dp, rt = state.stations, state.depots, state.routes
    cum50 = {(s, f): _cumulative(state, (s, f), horizon + SAFETY_TICKS + 1, low=False) for s in st for f in FUELS}
    cum10 = {(s, f): _cumulative(state, (s, f), horizon + 1, low=True) for s in st for f in FUELS}
    prob = pulp.LpProblem("allocation", pulp.LpMinimize)
    x: dict[tuple[str, str], pulp.LpVariable] = {}
    y: dict[tuple[str, str, int], pulp.LpVariable] = {}
    for rid, r in rt.items():
        s = st[r.destination_station_id]
        ok_now = s.status == "OPEN" and not route_blocked_for_departure(state, rid)
        for f in FUELS:
            room = max(0.0, float(s.capacity[f]) - float(s.inventory[f]))
            x[rid, f] = pulp.LpVariable(f"x_{rid}_{f}", 0, min(2 * r.max_shipment, room) if ok_now else 0)
            for j in SLOTS:
                ok = not _blocked_at(state, rid, t + j) and not _blocked_at(state, rid, t + j + 1)
                y[rid, f, j] = pulp.LpVariable(f"y_{rid}_{f}_{j}", 0, 2 * r.max_shipment if ok else 0)

    obj = []
    cover_terms = []
    z = pulp.LpVariable("z", 0, 10) if fair else None
    for sid, s in st.items():
        cap_all = s.capacity
        for f in FUELS:
            key = (sid, f)
            prev_u = None
            cap = float(cap_all[f])
            for m in CHECK:
                known = sum(i.quantity for i in state.inflight_for(key) if i.arrival_tick - t < m)
                arr_x = pulp.lpSum(x[rid, f] for rid, r in rt.items()
                                   if r.destination_station_id == sid and r.transit_ticks < m)
                arr_y = pulp.lpSum(y[rid, f, j] for rid, r in rt.items() for j in SLOTS
                                   if r.destination_station_id == sid and j + r.transit_ticks < m)
                supply_in = float(s.inventory[f]) + known + arr_x + arr_y
                u = pulp.LpVariable(f"u_{sid}_{f}_{m}", 0)
                o = pulp.LpVariable(f"o_{sid}_{f}_{m}", 0)
                inv = supply_in - cum50[key][m] + u
                prob += inv >= 0
                # overflow on the low-demand path (asymmetric risk: overflow happens when demand is LOW)
                prob += supply_in - cum10[key][m] - o <= cap * OVERFLOW_GUARD
                if prev_u is not None:
                    prob += u >= prev_u
                prev_u = u
                obj += [W_UNMET * u, W_OVERFLOW * o]
                safety = min(cap * 0.85, (cum50[key][m + SAFETY_TICKS] - cum50[key][m]) + SAFETY_NOISE * cum50[key][m])
                b = pulp.LpVariable(f"b_{sid}_{f}_{m}", 0)
                prob += b >= safety - inv
                obj.append(W_SAFETY * b)
                if m == CHECK[-1]:
                    target = max(min(cap * 0.9, cum50[key][horizon] - cum50[key][horizon - 32]), TERM_FILL * cap)
                    sh = pulp.LpVariable(f"sh_{sid}_{f}", 0)
                    prob += sh >= target - inv
                    obj.append(W_TERM * sh)
                    if fair and s.status == "OPEN":
                        cover_terms.append((inv, max(1.0, cum50[key][horizon] - cum50[key][horizon - 32])))

    for did, d in dp.items():
        for f in FUELS:
            reserve = reserve_fraction * float(d.capacity[f])
            for j in [0] + SLOTS:
                sup = sum(float(a.quantity) for a in state.supply if a.depot_id == did and a.fuel_type == f
                          and a.status != "ARRIVED" and a.planned_tick - t <= j)
                shipped = pulp.lpSum(x[rid, f] for rid, r in rt.items() if r.source_depot_id == did) + pulp.lpSum(
                    y[rid, f, jj] for rid, r in rt.items() for jj in SLOTS if r.source_depot_id == did and jj <= j)
                prob += shipped <= max(0.0, float(d.inventory[f]) - reserve) + sup
                clip = pulp.LpVariable(f"clip_{did}_{f}_{j}", 0)
                prob += float(d.inventory[f]) + sup - shipped - clip <= float(d.capacity[f])
                obj.append(W_DEPOT_CLIP * clip)
        prob += pulp.lpSum(x[rid, f] for rid, r in rt.items() if r.source_depot_id == did for f in FUELS) \
            <= max(0.0, dispatch_cap(state, did) - state.dispatched_this_tick.get(did, 0.0))
        for j in SLOTS:
            prob += pulp.lpSum(y[rid, f, j] for rid, r in rt.items() if r.source_depot_id == did for f in FUELS) \
                <= dispatch_cap(state, did)

    obj += [W_TRANSIT * rt[rid].transit_ticks * v for (rid, _f), v in x.items()]
    obj += [W_TRANSIT * rt[rid].transit_ticks * v for (rid, _f, _j), v in y.items()]
    if fair and cover_terms and z is not None:
        for inv, dem in cover_terms:
            prob += inv >= z * dem
        obj.append(-W_FAIR * z)
    prob += pulp.lpSum(obj)
    status = prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit))
    if pulp.LpStatus[status] != "Optimal":
        raise OptimizerFailed(f"solver status {pulp.LpStatus[status]}")
    return {k: float(v.value() or 0.0) for k, v in x.items()}


def plan(state: PlanningState, reserve_fraction: float = 0.0) -> Plan:
    shipments = solve(state, reserve_fraction)
    needs = assess_needs(state)
    reason_of = {n.key: n.reason for n in needs}
    critical = {n.key for n in needs if n.risk == "CRITICAL"}
    budget = Budget(state, reserve_fraction=reserve_fraction)
    lines: list[Line] = []
    dropped: list[str] = []
    # most urgent destinations first, so the budget favours them if a check trims anything
    order = sorted(shipments.items(), key=lambda kv: (kv[0][0], kv[0][1]))
    for (rid, f), q in order:
        r = state.routes[rid]
        key = (r.destination_station_id, f)
        while q >= MIN_LINE_LITERS:
            part = float(int(min(q, r.max_shipment) // 10) * 10)
            line = Line(rid, r.source_depot_id, r.destination_station_id, f, part,
                        reason=reason_of.get(key, f"{state.stations[key[0]].name} {f}: keeps cover over 24 h"),
                        critical=key in critical)
            violations = check_line(budget, line)
            if violations:
                dropped.append(f"{rid} {f} {part:.0f} L: {violations[0].message}")
                break
            budget.commit(line)
            lines.append(line)
            q -= part
    return Plan(lines=lines, policy_version=POLICY_VERSION, needs=needs, skipped=dropped)
