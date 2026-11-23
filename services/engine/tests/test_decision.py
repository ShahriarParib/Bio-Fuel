"""Constraints + heuristic on the real baseline world (fixtures captured from the simulator image)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.decision import heuristic
from app.decision.constraints import Budget, Line, check_line, route_blocked_for_departure
from app.decision.executor import decision_status_after_execution, outcome_from_error
from app.decision.planning import FUELS, Inflight, PlanningState, inventory_before_arrival, projected_inventory
from app.sim.errors import SimDomainError, SimUnavailable
from fsip_common.models import Depot, Region, Route, SimEvent, Station
from hypothesis import given, settings
from hypothesis import strategies as st

FX = Path(__file__).resolve().parents[3] / "libs" / "fsip_common" / "tests" / "fixtures"


def _load(name):
    return json.loads((FX / f"{name}.json").read_text())


def world(tick=0, events=(), inflight=(), inventories=None, demand=60.0, station_status=None) -> PlanningState:
    stations = {s["id"]: Station(**s) for s in _load("stations")}
    depots = {d["id"]: Depot(**d) for d in _load("depots")}
    routes = {r["id"]: Route(**r) for r in _load("routes")}
    regions = {r["id"]: Region(**r) for r in _load("regions")}
    for sid, inv in (inventories or {}).items():
        stations[sid] = stations[sid].model_copy(update={"inventory": {**stations[sid].inventory, **inv}})
    for sid, status in (station_status or {}).items():
        stations[sid] = stations[sid].model_copy(update={"status": status})
    p50 = {(s, f): [demand] * 96 for s in stations for f in FUELS}
    p10 = {k: [v[0] * 0.8] * 96 for k, v in p50.items()}
    return PlanningState(tick=tick, tick_minutes=15, stations=stations, depots=depots, routes=routes,
                         regions=regions, events=list(events), supply=[], inflight=list(inflight),
                         demand_p50=p50, demand_p10=p10)


def disruption(start, end, route_ids, status="SCHEDULED", eid=1):
    return SimEvent(id=eid, type="route_disruption", start_tick=start, end_tick=end, status=status,
                    parameters={"route_ids": route_ids})


# ---------------------------------------------------------------- rule 2: departure-time disruption
def test_disruption_starting_next_tick_blocks_route():
    s = world(tick=10, events=[disruption(11, 20, ["route-gazipur-mirpur"])])
    assert route_blocked_for_departure(s, "route-gazipur-mirpur")
    assert not route_blocked_for_departure(s, "route-gazipur-tongi")


def test_disruption_starting_later_does_not_block():
    s = world(tick=10, events=[disruption(14, 20, ["route-gazipur-mirpur"])])
    assert not route_blocked_for_departure(s, "route-gazipur-mirpur")


def test_empty_route_filter_means_every_route():
    s = world(tick=10, events=[disruption(10, 20, [], status="ACTIVE")])
    assert all(route_blocked_for_departure(s, r) for r in s.routes)


def test_resolved_disruption_is_ignored():
    s = world(tick=10, events=[disruption(5, 20, ["route-gazipur-mirpur"], status="RESOLVED")])
    assert not route_blocked_for_departure(s, "route-gazipur-mirpur")


# ---------------------------------------------------------------- rule 6: shared dispatch cap
def test_dispatch_cap_is_shared_across_fuels():
    s = world(inventories={"station-mirpur": {"DIESEL": 2000, "PETROL": 2000},
                           "station-tongi": {"DIESEL": 2000}})
    b = Budget(s)
    lines = [Line("route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "DIESEL", 6000),
             Line("route-gazipur-tongi", "depot-gazipur", "station-tongi", "DIESEL", 6000)]
    for ln in lines:
        assert check_line(b, ln) == []
        b.commit(ln)
    extra = Line("route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "PETROL", 500)
    assert [v.rule for v in check_line(b, extra)] == ["DISPATCH_CAPACITY"]


# ---------------------------------------------------------------- rule 7: overflow with in-flight fuel
def test_inflight_fuel_counts_against_room_at_arrival():
    # 9,000 L now, cap 15,000; 5,000 L already arriving at tick 2 → a new 3,000 L line would overflow
    s = world(inventories={"station-mirpur": {"DIESEL": 9000}},
              inflight=[Inflight("station-mirpur", "DIESEL", 2, 5000)])
    line = Line("route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "DIESEL", 3000)
    rules = [v.rule for v in check_line(Budget(s), line)]
    assert "OVERFLOW_AT_ARRIVAL" in rules
    assert "STATION_CAPACITY_NOW" not in rules  # the simulator itself would have accepted it: the trap


def test_projection_applies_arrivals_and_clamps_at_zero():
    s = world(inventories={"station-tongi": {"DIESEL": 100}}, inflight=[Inflight("station-tongi", "DIESEL", 3, 1000)])
    path = projected_inventory(s, ("station-tongi", "DIESEL"), 5)
    assert path[0] == 100 and path[2] == 0.0          # runs dry before the arrival
    assert path[4] == pytest.approx(1000 - 60)        # arrival at tick 3 minus that tick's demand
    assert inventory_before_arrival(s, ("station-tongi", "DIESEL"), 3) == pytest.approx(1000)


# ---------------------------------------------------------------- heuristic
def test_heuristic_refills_a_low_station_and_every_line_is_feasible():
    s = world(inventories={"station-mirpur": {"DIESEL": 1500}})
    p = heuristic.plan(s)
    mirpur = [ln for ln in p.lines if ln.destination_station_id == "station-mirpur" and ln.fuel_type == "DIESEL"]
    assert mirpur and mirpur[0].route_id == "route-gazipur-mirpur"  # shortest route first
    _assert_plan_feasible(s, p.lines)


def test_heuristic_skips_outage_station_and_blocked_single_route():
    s = world(tick=10, events=[disruption(10, 30, ["route-gazipur-tongi"], status="ACTIVE")],
              inventories={"station-tongi": {"DIESEL": 500}, "station-coxsbazar": {"PETROL": 500}},
              station_status={"station-coxsbazar": "OUTAGE"})
    p = heuristic.plan(s)
    assert not [ln for ln in p.lines if ln.destination_station_id in ("station-tongi", "station-coxsbazar")]
    assert any("tongi" in m.lower() for m in p.skipped)


def _assert_plan_feasible(s: PlanningState, lines: list[Line]) -> None:
    b = Budget(s)
    for ln in lines:
        assert check_line(b, ln) == [], ln
        b.commit(ln)


@settings(max_examples=60, deadline=None)
@given(st.lists(st.floats(min_value=0.0, max_value=1.0), min_size=12, max_size=12),
       st.floats(min_value=10, max_value=400))
def test_property_any_world_any_demand_plan_is_feasible(fills, demand):
    stations = sorted({r["destination_station_id"] for r in _load("routes")})
    caps = {s["id"]: s["capacity"] for s in _load("stations")}
    inv = {sid: {f: fills[i * 3 + j] * caps[sid][f] for j, f in enumerate(FUELS)} for i, sid in enumerate(stations)}
    s = world(inventories=inv, demand=demand)
    _assert_plan_feasible(s, heuristic.plan(s).lines)


# ---------------------------------------------------------------- executor mapping
def test_executor_maps_simulator_outcomes():
    assert outcome_from_error(SimDomainError(409, "DISPATCH_CAPACITY_EXCEEDED", "")).status == "DEFERRED"
    assert outcome_from_error(SimDomainError(409, "ROUTE_DISRUPTED", "")).status == "REJECTED_BY_SIM"
    assert outcome_from_error(SimUnavailable("down")).status == "DEFERRED"
    assert decision_status_after_execution(["SUBMITTED", "SUBMITTED"]) == "EXECUTED"
    assert decision_status_after_execution(["SUBMITTED", "DEFERRED"]) == "PARTIAL"
    assert decision_status_after_execution(["REJECTED_BY_SIM"]) == "FAILED"


def test_shipment_impact_is_measured_from_arrival_and_drops_sharply():
    from app.risk.stockout import shipment_impact

    s = world(inventories={"station-mirpur": {"DIESEL": 300}}, demand=100)
    imp = shipment_impact(s, ("station-mirpur", "DIESEL"), arrival_tick=2, qty=6000, run_id=1, p90={})
    assert imp["stockout_prob_before"] > 0.9
    assert imp["stockout_prob_after"] < 0.2
    assert imp["unmet_after"] < imp["unmet_before"]


def test_degraded_reasons_clear_when_data_returns():
    from app.sim.client import SimResult
    from app.state.store import WorldState

    ws = WorldState()
    ws.apply_snapshot({"instance": SimUnavailable("boot")})
    assert any(r.startswith("missing_resources:") for r in ws.degraded_reasons)
    ok = {name: SimResult(data=_load(fx), stale=False, status=200, latency_ms=1.0) for name, fx in (
        ("instance", "instance"), ("regions", "regions"), ("depots", "depots"), ("stations", "stations"),
        ("routes", "routes"), ("events", "events"), ("supply_arrivals", "supply_arrivals"),
        ("allocations", "allocations"), ("metrics", "metrics"))}
    ws.apply_snapshot(ok)
    assert ws.snapshot is not None
    assert not ws.degraded_reasons


def test_optimizer_plan_is_feasible_and_refills_the_low_station(monkeypatch):
    from app.decision import optimizer

    # 300 L at 60 L/tick = 5 ticks of cover: shipping later would be too late, so the MPC must ship now
    s = world(inventories={"station-mirpur": {"DIESEL": 300}, "station-tongi": {"PETROL": 800}})
    p = optimizer.plan(s)
    assert p.policy_version == "optimizer@1.0.0"
    assert any(ln.destination_station_id == "station-mirpur" and ln.fuel_type == "DIESEL" for ln in p.lines), p.skipped
    _assert_plan_feasible(s, p.lines)


def test_optimizer_respects_a_cut_single_route():
    from app.decision import optimizer

    s = world(tick=10, events=[disruption(10, 40, ["route-gazipur-tongi"], status="ACTIVE")],
              inventories={"station-tongi": {"DIESEL": 500}})
    assert not [ln for ln in optimizer.plan(s).lines if ln.route_id == "route-gazipur-tongi"]


def test_optimizer_force_fail_drill(monkeypatch):
    from app.decision import optimizer

    monkeypatch.setenv("OPTIMIZER_FORCE_FAIL", "true")
    with pytest.raises(optimizer.OptimizerFailed):
        optimizer.solve(world())
