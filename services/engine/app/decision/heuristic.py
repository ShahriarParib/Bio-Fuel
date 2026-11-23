"""Greedy heuristic policy (05 §4.2): deterministic, fast, and the fallback for the optimizer.

Benchmark (greedy, real simulator): service level 1.000 on the 3-day baseline, 0.992 in the combined crisis.
This version adds the in-flight-aware overflow guard and the departure-time disruption guard from constraints.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .constraints import MIN_LINE_LITERS, Budget, Line, check_line, route_blocked_for_departure
from .planning import FUELS, Key, PlanningState, hours_to_stockout, projected_inventory

POLICY_VERSION = "heuristic@1.0.0"
REORDER_FILL = 0.60
TARGET_FILL = 0.90
RISK_RANK = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}


@dataclass
class Need:
    key: Key
    risk: str
    hours_to_stockout: float | None
    fill_with_inflight: float
    reason: str


@dataclass
class Plan:
    lines: list[Line]
    policy_version: str
    needs: list[Need] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # human-readable reasons a need could not be served


def lead_ticks(state: PlanningState, station_id: str) -> int | None:
    """Shortest usable route transit to this station + 1 tick (departure step)."""
    transits = [r.transit_ticks for r in state.routes.values()
                if r.destination_station_id == station_id and not route_blocked_for_departure(state, r.id)]
    return min(transits) + 1 if transits else None


def risk_level(state: PlanningState, key: Key) -> tuple[str, float | None]:
    """Deterministic risk from the p50 projection (B3 replaces it with Monte Carlo probability)."""
    hts = hours_to_stockout(state, key, horizon=96)
    if hts is None:
        return "LOW", None
    lead = lead_ticks(state, key[0])
    lead_h = (lead if lead is not None else 8) / state.ticks_per_hour
    if hts <= lead_h + 0.5:
        return "CRITICAL", hts
    if hts <= 6:
        return "HIGH", hts
    if hts <= 12:
        return "MEDIUM", hts
    return "LOW", hts


def assess_needs(state: PlanningState) -> list[Need]:
    needs: list[Need] = []
    for sid, st in state.stations.items():
        for f in FUELS:
            key = (sid, f)
            cap = float(st.capacity[f])
            inflight = sum(i.quantity for i in state.inflight_for(key))
            fill = (float(st.inventory[f]) + inflight) / cap if cap else 1.0
            risk, hts = risk_level(state, key)
            if fill < REORDER_FILL or RISK_RANK[risk] >= RISK_RANK["HIGH"]:
                eta = f"stockout in {hts:.1f} h" if hts is not None else "no stockout within 24 h"
                needs.append(Need(key, risk, hts, fill,
                                  f"{st.name} {f} {risk}: {fill:.0%} full incl. in-flight, {eta}"))
    # most urgent first; ids make the order deterministic
    needs.sort(key=lambda n: (-RISK_RANK[n.risk], n.hours_to_stockout if n.hours_to_stockout is not None else 1e9,
                              n.fill_with_inflight, n.key))
    return needs


def _depot_days_of_supply(state: PlanningState, depot_id: str, fuel: str) -> float:
    return float(state.depots[depot_id].inventory[fuel])


def plan(state: PlanningState, reserve_fraction: float = 0.0) -> Plan:
    budget = Budget(state, reserve_fraction=reserve_fraction)
    lines: list[Line] = []
    skipped: list[str] = []
    needs = assess_needs(state)
    for need in needs:
        sid, f = need.key
        st = state.stations[sid]
        if st.status != "OPEN":
            skipped.append(f"{st.name} {f}: station in outage, shipping impossible")
            continue
        cap = float(st.capacity[f])
        routes = sorted(
            (r for r in state.routes.values() if r.destination_station_id == sid),
            key=lambda r: (r.transit_ticks, -_depot_days_of_supply(state, r.source_depot_id, f), r.id),
        )
        served = False
        for r in routes:
            if route_blocked_for_departure(state, r.id):
                continue
            # target: fill to 90% (p50 path) at arrival; hard cap: room at arrival on the p10 path
            p50_before = projected_inventory(state, need.key, r.transit_ticks)[r.transit_ticks]
            p50_before += sum(i.quantity for i in budget.added
                              if (i.station_id, i.fuel_type) == need.key)
            want = TARGET_FILL * cap - p50_before
            for _ in range(2):  # at most two lines per route (max_shipment is per allocation)
                q = min(want, r.max_shipment, budget.depot_left.get((r.source_depot_id, f), 0.0),
                        budget.dispatch_left(r.source_depot_id),
                        budget.room_at_arrival(r.id, sid, f), budget.room_now(sid, f) - _pending_to(lines, sid, f))
                q = float(int(q // 10) * 10)  # round down to 10 L
                critical = need.risk == "CRITICAL"
                if q < MIN_LINE_LITERS and not (critical and q > 0):
                    break
                line = Line(r.id, r.source_depot_id, sid, f, q, reason=need.reason, critical=critical)
                if check_line(budget, line):
                    break
                budget.commit(line)
                lines.append(line)
                want -= q
                served = True
                if want < MIN_LINE_LITERS:
                    break
            if want < MIN_LINE_LITERS:
                break
        if not served:
            skipped.append(f"{need.reason}: no feasible route/depot capacity this tick")
    return Plan(lines=lines, policy_version=POLICY_VERSION, needs=needs, skipped=skipped)


def _pending_to(lines: list[Line], station_id: str, fuel: str) -> float:
    """Liters already planned this tick to the station: the simulator's capacity check sums them with inventory."""
    return sum(ln.quantity for ln in lines if ln.destination_station_id == station_id and ln.fuel_type == fuel)
