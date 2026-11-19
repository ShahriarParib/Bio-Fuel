"""Contract tests: models must parse REAL simulator responses (fixtures captured by scripts/capture_fixtures.sh)
and must reject malformed variants (the organizers' 'malformed fields' inject)."""
import copy
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from fsip_common.models import (Allocation, DemandObservation, Depot, Health, Instance, Region, Route, SimEvent,
                                SimMetrics, Station, SupplyArrival, world_issues)
from fsip_common.timeutil import expected_arrival_tick, format_sim_clock, parse_sim_time

FX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FX / f"{name}.json").read_text())


@pytest.mark.parametrize("name,model,many", [
    ("health", Health, False), ("instance", Instance, False), ("regions", Region, True),
    ("depots", Depot, True), ("depot_single", Depot, False), ("stations", Station, True),
    ("routes", Route, True), ("supply_arrivals", SupplyArrival, True), ("events", SimEvent, True),
    ("allocations", Allocation, True), ("allocation_created_201", Allocation, False),
    ("demand_history", DemandObservation, True), ("metrics", SimMetrics, False),
])
def test_real_fixtures_parse(name, model, many):
    data = load(name)
    if many:
        items = TypeAdapter(list[model]).validate_python(data)
        assert len(items) > 0
    else:
        model.model_validate(data)


def test_world_is_consistent():
    depots = TypeAdapter(list[Depot]).validate_python(load("depots"))
    stations = TypeAdapter(list[Station]).validate_python(load("stations"))
    routes = TypeAdapter(list[Route]).validate_python(load("routes"))
    regions = TypeAdapter(list[Region]).validate_python(load("regions"))
    assert world_issues(depots, stations, routes, regions) == []
    assert len(depots) == 2 and len(stations) == 4 and len(routes) == 6


def test_idempotent_replay_returns_same_allocation():
    assert load("allocation_created_201")["id"] == load("allocation_replay_201")["id"]


@pytest.mark.parametrize("mutate", [
    lambda s: s["inventory"].pop("OCTANE"),                     # missing fuel key
    lambda s: s["inventory"].__setitem__("DIESEL", -5),        # negative
    lambda s: s["inventory"].__setitem__("DIESEL", "lots"),    # wrong type
    lambda s: s.__setitem__("status", "EXPLODED"),             # unknown enum
    lambda s: s.pop("capacity"),                               # missing field
    lambda s: s.__setitem__("demand_multiplier", float("nan")),  # NaN
])
def test_malformed_station_rejected(mutate):
    st = copy.deepcopy(load("stations")[0])
    mutate(st)
    with pytest.raises(ValidationError):
        Station.model_validate(st)


def test_extra_fields_ignored():
    st = copy.deepcopy(load("stations")[0])
    st["brand_new_field"] = 123
    Station.model_validate(st)


def test_world_issues_detects_bad_route_and_overfull():
    depots = TypeAdapter(list[Depot]).validate_python(load("depots"))
    stations = TypeAdapter(list[Station]).validate_python(load("stations"))
    routes = TypeAdapter(list[Route]).validate_python(load("routes"))
    routes[0].source_depot_id = "depot-ghost"
    stations[0].inventory["DIESEL"] = stations[0].capacity["DIESEL"] * 2
    issues = world_issues(depots, stations, routes)
    assert any("depot-ghost" in i for i in issues) and any("> capacity" in i for i in issues)


def test_error_envelopes_have_expected_shapes():
    assert load("error_409_idempotency_mismatch")["detail"]["code"] == "IDEMPOTENCY_KEY_MISMATCH"
    assert load("error_409_route_mismatch")["detail"]["code"] == "ROUTE_MISMATCH"
    assert load("error_404_not_found")["detail"]["code"] == "NOT_FOUND"
    assert isinstance(load("error_422_validation")["detail"], list)
    assert load("error_503_fault_injected")["error"]["code"] == "FAULT_INJECTED"
    assert "x-simulator-stale: true" in (FX / "stale_headers.txt").read_text().lower()


def test_event_helpers():
    ev = SimEvent(id=1, type="route_disruption", start_tick=5, end_tick=10, status="SCHEDULED", parameters={})
    assert ev.targets("route_ids") == []          # empty filter == ALL routes
    assert ev.covers_tick(5) and ev.covers_tick(9) and not ev.covers_tick(10)


def test_timeutil():
    assert parse_sim_time("2026-01-01T00:15:00").tzinfo is not None
    assert parse_sim_time("2026-01-01T00:15:00+00:00") == parse_sim_time("2026-01-01T00:15:00")
    assert expected_arrival_tick(0, 2) == 2
    assert format_sim_clock(140, 15) == "Day 2 11:00 · t=140"
