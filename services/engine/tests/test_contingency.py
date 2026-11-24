"""The bridge from the engine's planning view to the twin, and the advice it produces.

The twin's own behaviour is tested in `libs/fsip_common/tests/test_twin.py`. What matters here is that we hand it
the *real* network truthfully: a disrupted route is today's reality and belongs in the baseline, a CONSTRAINED
depot dispatches less, and in-flight fuel is already on its way.
"""
from __future__ import annotations

from app.intelligence.contingency import scan, world_from
from fsip_common.models import Depot, Route
from test_decision import world as fixture_world  # the real captured network


def _state(**kw):
    return fixture_world(**kw)


class TestWorldTranslation:
    def test_a_disrupted_route_is_unavailable_in_the_twin(self):
        """A route that is already cut is not a contingency we are asking about - it is the baseline."""
        st = _state()
        rid = next(iter(st.routes))
        st.routes[rid] = Route(**{**st.routes[rid].model_dump(), "status": "DISRUPTED"})
        world = world_from(st)
        assert next(r for r in world.routes if r.id == rid).available is False

    def test_a_constrained_depot_dispatches_less(self):
        """ADR-15: the simulator does not reduce the cap, but the status signals reduced capacity."""
        st = _state()
        did = next(iter(st.depots))
        full = world_from(st)
        normal = next(d for d in full.depots if d.id == did).dispatch_per_tick
        st.depots[did] = Depot(**{**st.depots[did].model_dump(), "status": "CONSTRAINED"})
        constrained = next(d for d in world_from(st).depots if d.id == did).dispatch_per_tick
        assert constrained == normal * 0.5

    def test_fuel_already_in_transit_is_carried_into_the_projection(self):
        st = _state()
        world = world_from(st)
        assert len(world.inflight) == len(st.inflight)

    def test_only_future_supply_is_carried(self):
        st = _state()
        world = world_from(st)
        assert all(s.tick >= st.tick for s in world.supply)

    def test_every_station_depot_and_route_crosses_over(self):
        st = _state()
        world = world_from(st)
        assert len(world.stations) == len(st.stations)
        assert len(world.depots) == len(st.depots)
        assert len(world.routes) == len(st.routes)


class TestScanOutput:
    def test_it_reports_a_finding_per_asset_and_duration(self):
        doc = scan(_state())
        assets = len(doc["findings"]) / len(doc["durations"])
        assert assets == len(_state().stations) + len(_state().depots) + len(_state().routes)

    def test_findings_carry_a_human_label_not_only_an_id(self):
        doc = scan(_state())
        for f in doc["findings"]:
            assert f["label"] and not f["label"].startswith(("route-", "depot-", "station-"))

    def test_the_top_findings_carry_advice_an_operator_can_act_on(self):
        doc = scan(_state())
        for f in doc["worst_assets"]:
            assert f["advice"] and len(f["advice"]) > 20
            assert "{" not in f["advice"]

    def test_it_records_that_the_forecast_was_degraded(self):
        st = _state()
        st.stale = True
        assert scan(st)["stale"] is True

    def test_it_is_fast_enough_for_a_tick(self):
        doc = scan(_state())
        assert doc["computed_in_ms"] < 3000

    def test_a_region_only_fixture_still_produces_a_baseline(self):
        doc = scan(_state())
        assert "unmet_liters" in doc["baseline"]
        assert doc["horizon_ticks"] == 96


def test_the_scan_runs_on_the_real_captured_network():
    """Not a synthetic graph: these are the stations, depots and routes captured from the simulator image."""
    doc = scan(_state())
    assets = {f["asset"] for f in doc["findings"]}
    assert any(a.startswith("route-") for a in assets)
    assert any(a.startswith("depot-") for a in assets)
    assert any(a.startswith("station-") for a in assets)
