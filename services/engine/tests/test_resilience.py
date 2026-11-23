"""B6: health judges, mode machine, incident lifecycle, break-glass rendering (pure, no I/O)."""
from __future__ import annotations

from types import SimpleNamespace

from app.state import breakglass
from app.state.health import (
    DEGRADED,
    DOWN,
    UP,
    Component,
    IncidentTracker,
    compute_mode,
    judge_data_quality,
    judge_simulator,
    unreachable_stations,
)

OK = {"requests": 30, "error_rate": 0.0, "p95_ms": 12.0, "last_ok_age_s": 0.4}


def test_simulator_judged_from_traffic_not_health_endpoint():
    assert judge_simulator(OK, "CLOSED", False).status == UP
    assert judge_simulator(OK, "OPEN", False).status == DOWN
    assert judge_simulator({**OK, "last_ok_age_s": 25.0}, "CLOSED", False).status == DOWN
    assert judge_simulator({**OK, "error_rate": 0.95}, "CLOSED", False).status == DOWN
    flaky = judge_simulator({**OK, "error_rate": 0.4}, "CLOSED", False)
    assert flaky.status == DEGRADED and "40%" in flaky.detail
    assert judge_simulator({**OK, "p95_ms": 1800.0}, "CLOSED", False).status == DEGRADED
    stale = judge_simulator(OK, "CLOSED", True)
    assert stale.status == DEGRADED and "STALE" in stale.detail


def test_data_quality_names_the_field_path():
    assert judge_data_quality({}).status == UP
    c = judge_data_quality({"stations": "0.inventory.DIESEL: Input should be a valid number"})
    assert c.status == DEGRADED and "stations" in c.detail and "inventory.DIESEL" in c.detail


def _comps(**over):
    base = {k: Component(UP) for k in ("simulator", "sse", "intelligence", "db", "redis", "optimizer", "data_quality")}
    base.update(over)
    return base


def test_mode_precedence():
    assert compute_mode(_comps(), 0, False, []) == ("NORMAL", [])
    mode, reasons = compute_mode(_comps(intelligence=Component(DOWN, "down")), 0, False, [])
    assert mode == "DEGRADED" and reasons == ["intelligence: down"]
    assert compute_mode(_comps(), 0, True, [])[0] == "DEGRADED"  # stale alone degrades
    down = _comps(simulator=Component(DOWN, "unreachable"))
    assert compute_mode(down, 59.0, False, [])[0] == "DEGRADED"
    mode, reasons = compute_mode(down, 61.0, False, [])
    assert mode == "SAFE_HOLD" and "61 s" in reasons[0]
    assert compute_mode(_comps(), 0, False, ["Tongi"])[0] == "NO_FEASIBLE_ROUTES"
    assert compute_mode(down, 61.0, False, ["Tongi"])[0] == "SAFE_HOLD"  # SAFE_HOLD wins


def test_incident_lifecycle_with_hysteresis():
    t = IncidentTracker(open_after=2, close_after=3)
    bad = _comps(simulator=Component(DOWN, "unreachable"))
    assert t.update(bad, 100.0, "DEGRADED") == []          # one bad sample: not yet
    opened = t.update(bad, 101.0, "DEGRADED")
    assert len(opened) == 1 and opened[0]["status"] == "OPEN"
    assert [e["kind"] for e in opened[0]["timeline"]] == ["detected", "mitigated"]
    t.update(bad, 102.0, "SAFE_HOLD")                       # mode escalation recorded
    assert t.open["simulator"]["timeline"][-1]["kind"] == "mode"
    good = _comps()
    t.update(good, 103.0, "NORMAL")
    t.update(bad, 104.0, "DEGRADED")                        # flap: still the same incident
    assert len(t.items) == 1
    for ts in (105.0, 106.0):
        t.update(good, ts, "NORMAL")
    assert t.open                                           # 2 good samples: still open
    t.update(good, 107.0, "NORMAL")
    inc = t.items[0]
    assert inc["status"] == "RESOLVED" and inc["mttr_s"] == 6.0
    doc = t.document()
    assert doc["open"] == 0 and "_last_mode" not in doc["items"][0]


def test_unreachable_stations():
    routes = [SimpleNamespace(id="r1", destination_station_id="s1", status="DISRUPTED"),
              SimpleNamespace(id="r2", destination_station_id="s1", status="AVAILABLE"),
              SimpleNamespace(id="r3", destination_station_id="s2", status="AVAILABLE")]
    snap = SimpleNamespace(routes=routes, stations=[SimpleNamespace(id="s1", name="Tongi"),
                                                    SimpleNamespace(id="s2", name="Mirpur")])
    cut = SimpleNamespace(type="route_disruption", status="SCHEDULED", start_tick=11, end_tick=30,
                          parameters={"route_ids": ["r2"]})
    assert unreachable_stations(snap, [], 10) == []
    assert unreachable_stations(snap, [cut], 10) == ["Tongi"]  # disrupted at departure counts


def test_breakglass_render_is_static_and_escaped():
    state = {"tick": 40, "clock_label": "Day 1 10:00", "sim_run_id": 3, "engine_mode": "SAFE_HOLD",
             "stations": [{"id": "s1", "name": "Tongi <x>", "inventory": {"DIESEL": 100},
                           "capacity": {"DIESEL": 1000}}],
             "depots": [{"id": "d1", "name": "Gazipur"}],
             "cover": {"stations": [{"station_id": "s1", "fuel_type": "DIESEL", "hours_of_cover": 2.5}]},
             "pending_recommendations": [{"id": 7, "lines": [{"source_depot_id": "d1", "destination_station_id": "s1",
                                                              "fuel_type": "DIESEL", "quantity": 4000,
                                                              "route_id": "r1"}]}],
             "allocations_active": [], "routes": [{"id": "r9", "status": "DISRUPTED", "source_depot_id": "d1",
                                                   "destination_station_id": "s1"}]}
    page, csv_text = breakglass.render(state)
    assert "<script" not in page and "Tongi &lt;x&gt;" in page and "SAFE_HOLD" in page and "r9" in page
    assert "RECOMMENDED,7,Gazipur,Tongi <x>,DIESEL,4000,r1" in csv_text
