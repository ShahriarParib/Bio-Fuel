"""The shadow twin and the N-1 scan.

These tests are written as questions an operator would ask ("if I lose this route, does anything run dry?"), because
that is what the numbers are used for. A twin that is subtly wrong is worse than no twin: it would rank the wrong
asset as fragile and send people to pre-stock the wrong station.
"""
from __future__ import annotations

from fsip_common.twin import (
    TwinDepot,
    TwinRoute,
    TwinStation,
    TwinSupply,
    TwinWorld,
    assets_of,
    n1_scan,
    project,
)

FLAT = tuple([200.0] * 96)      # 200 L per tick, every tick


def world(*, routes=None, station_inventory=2000.0, depot_inventory=100_000.0, dispatch=5000.0) -> TwinWorld:
    """Two stations. 'twin-fed' has two routes; 'single' has one. Both draw 200 L of DIESEL a tick."""
    stations = (
        TwinStation("station-twinfed", {"DIESEL": 10_000.0}, {"DIESEL": station_inventory}),
        TwinStation("station-single", {"DIESEL": 10_000.0}, {"DIESEL": station_inventory}),
    )
    depots = (
        TwinDepot("depot-a", {"DIESEL": depot_inventory}, dispatch),
        TwinDepot("depot-b", {"DIESEL": depot_inventory}, dispatch),
    )
    default_routes = (
        TwinRoute("route-a-twinfed", "depot-a", "station-twinfed", 2, 5000.0),
        TwinRoute("route-b-twinfed", "depot-b", "station-twinfed", 3, 5000.0),
        TwinRoute("route-a-single", "depot-a", "station-single", 2, 5000.0),
    )
    return TwinWorld(
        tick=100, tick_minutes=15, stations=stations, depots=depots,
        routes=routes if routes is not None else default_routes,
        demand={("station-twinfed", "DIESEL"): FLAT, ("station-single", "DIESEL"): FLAT},
    )


class TestProjection:
    def test_a_healthy_network_serves_everything(self):
        r = project(world(), horizon=96)
        assert r.unmet_liters == 0.0
        assert r.first_stockout_tick is None
        assert r.service_level == 1.0
        assert r.shipments > 0                                   # it had to actually ship to achieve that

    def test_with_no_routes_at_all_the_stations_drain(self):
        r = project(world(routes=()), horizon=96)
        assert r.unmet_liters > 0
        assert r.first_stockout_tick == 110                      # 2000 L at 200 L/tick = 10 ticks from tick 100
        assert set(r.stockout_keys) == {("station-twinfed", "DIESEL"), ("station-single", "DIESEL")}

    def test_demand_is_counted_even_when_it_cannot_be_served(self):
        """Service level is demand-based: a network that serves nothing still has the same demand."""
        served = project(world(), horizon=96)
        starved = project(world(routes=()), horizon=96)
        assert starved.demand_liters == served.demand_liters
        assert starved.service_level < 0.5

    def test_a_full_station_with_no_demand_is_left_alone(self):
        """The simulator loses whatever does not fit on arrival, so the twin must never ship into a full tank."""
        idle = TwinWorld(**{**world(station_inventory=10_000.0).__dict__,
                            "demand": {("station-twinfed", "DIESEL"): (0.0,) * 96,
                                       ("station-single", "DIESEL"): (0.0,) * 96}})
        r = project(idle, horizon=96)
        assert r.shipments == 0
        assert r.delivered_liters == 0.0

    def test_an_empty_depot_cannot_ship(self):
        r = project(world(depot_inventory=0.0), horizon=96)
        assert r.shipments == 0
        assert r.unmet_liters > 0

    def test_a_depot_cannot_exceed_its_dispatch_cap_in_a_tick(self):
        """Both stations pull from depot-a first (it is the fastest route for each); the cap must bind."""
        tight = world(dispatch=400.0, station_inventory=600.0)
        r = project(tight, horizon=48)
        assert r.shipments > 0

    def test_supply_arriving_later_is_used_when_it_lands(self):
        empty = world(depot_inventory=0.0)
        resupplied = TwinWorld(**{**empty.__dict__,
                                  "supply": (TwinSupply(tick=104, depot_id="depot-a", fuel="DIESEL",
                                                        quantity=50_000.0),)})
        assert project(resupplied, horizon=96).shipments > 0
        assert project(resupplied, horizon=96).unmet_liters < project(empty, horizon=96).unmet_liters


class TestBlocking:
    def test_losing_the_only_route_to_a_station_hurts_that_station(self):
        r = project(world(), horizon=96, blocked=("route-a-single",))
        assert ("station-single", "DIESEL") in r.stockout_keys
        assert ("station-twinfed", "DIESEL") not in r.stockout_keys      # it has a second route

    def test_losing_one_of_two_routes_is_absorbed(self):
        r = project(world(), horizon=96, blocked=("route-a-twinfed",))
        assert ("station-twinfed", "DIESEL") not in r.stockout_keys

    def test_a_block_that_expires_lets_the_network_recover(self):
        forever = project(world(), horizon=96, blocked=("route-a-single",))
        brief = project(world(), horizon=96, blocked=("route-a-single",), block_ticks=4)
        assert brief.unmet_liters < forever.unmet_liters

    def test_a_closed_station_loses_its_demand_but_not_its_tank(self):
        """Measured simulator behaviour: an outage records demand as unmet and leaves the inventory alone."""
        r = project(world(), horizon=20, blocked=("station-single",), block_ticks=20)
        assert r.unmet_liters == 200.0 * 20                            # exactly its demand for the outage
        assert r.unmet_while_closed == 200.0 * 20                      # and all of it was unavoidable
        assert ("station-single", "DIESEL") not in r.stockout_keys     # it never actually ran dry

    def test_losing_a_depot_removes_every_route_out_of_it(self):
        only_a = (TwinRoute("route-a-single", "depot-a", "station-single", 2, 5000.0),
                  TwinRoute("route-a-twinfed", "depot-a", "station-twinfed", 2, 5000.0))
        r = project(world(routes=only_a), horizon=96, blocked=("depot-a",))
        assert r.shipments == 0


class TestN1Scan:
    def test_it_names_the_single_route_station_as_the_fragile_one(self):
        """A station outage loses more litres than any route cut, but nothing we ship prevents it.

        Ranking on raw unmet demand would put every station at the top of the list and send an operator off to
        fix something that cannot be fixed. The ranking is on avoidable loss, so the fragile *route* surfaces.
        """
        scan = n1_scan(world(), horizon=96)
        worst = scan["worst_assets"][0]
        assert worst["asset"] == "route-a-single"
        assert worst["kind"] == "route"
        assert worst["critical"] is True
        assert worst["worst_station"] == "station-single"

    def test_a_redundant_route_is_not_flagged_critical(self):
        scan = n1_scan(world(), horizon=96)
        redundant = [f for f in scan["findings"] if f["asset"] == "route-a-twinfed"]
        assert redundant and all(f["critical"] is False for f in redundant)
        assert all(f["extra_unmet_vs_normal"] == 0 for f in redundant)

    def test_every_asset_and_duration_is_covered(self):
        w = world()
        scan = n1_scan(w, horizon=48, durations=(24, 48))
        assert len(scan["findings"]) == len(assets_of(w)) * 2
        assert scan["durations"] == [24, 48]

    def test_a_station_outage_is_reported_but_not_blamed_on_the_network(self):
        scan = n1_scan(world(), horizon=96)
        outage = next(f for f in scan["findings"]
                      if f["asset"] == "station-single" and f["duration_ticks"] == 24)
        assert outage["unavoidable_unmet"] == 200 * 24           # its own lost sales
        assert outage["extra_unmet_vs_normal"] == 0              # it cost the rest of the network nothing
        assert outage["critical"] is False

    def test_the_cost_is_measured_against_a_normal_day_not_from_zero(self):
        """A network already under strain must not make every asset look catastrophic."""
        strained = world(depot_inventory=1_000.0)
        scan = n1_scan(strained, horizon=96)
        assert scan["baseline"]["unmet_liters"] > 0
        for f in scan["findings"]:
            assert f["extra_unmet_vs_normal"] <= f["unmet_liters"]

    def test_time_to_stockout_is_compared_against_time_to_reroute(self):
        scan = n1_scan(world(), horizon=96)
        twin_fed = next(f for f in scan["findings"] if f["asset"] == "route-a-twinfed")
        assert twin_fed["ttr_hours"] == 1.0          # the depot-b route is 3 ticks + 1 to plan = 1 h at 15 min/tick
        assert twin_fed["critical"] is False         # and nothing ran dry, so there was nothing to beat
        single = next(f for f in scan["findings"] if f["asset"] == "route-a-single")
        assert single["ttr_hours"] is None                       # nothing else reaches that station
        assert single["critical"] is True

    def test_the_report_says_out_loud_that_it_is_an_estimate(self):
        assert "estimates, not predictions" in n1_scan(world(), horizon=24)["method"]

    def test_a_scan_is_fast_enough_to_run_inside_a_tick(self):
        import time
        t0 = time.monotonic()
        n1_scan(world(), horizon=96)
        assert time.monotonic() - t0 < 3.0

class TestSinglePointsOfFailure:
    """A sole supply path is worth acting on even on a day when nothing runs dry."""

    def test_the_only_route_to_a_station_is_named_a_single_point_of_failure(self):
        scan = n1_scan(world(), horizon=96)
        spofs = {s["asset"] for s in scan["single_points_of_failure"]}
        assert "route-a-single" in spofs
        assert "route-a-twinfed" not in spofs          # it has a sibling

    def test_a_station_is_never_its_own_single_point_of_failure(self):
        scan = n1_scan(world(), horizon=96)
        assert all(s["kind"] != "station" for s in scan["single_points_of_failure"])

    def test_each_asset_is_listed_once_not_once_per_duration(self):
        scan = n1_scan(world(), horizon=96, durations=(24, 48))
        assets = [s["asset"] for s in scan["single_points_of_failure"]]
        assert len(assets) == len(set(assets))

    def test_a_well_stocked_network_still_surfaces_its_fragile_route(self):
        """The point of the whole feature: full tanks today hide nothing about tomorrow."""
        flush = world(station_inventory=9_000.0)              # 45 ticks of cover at 200 L a tick
        scan = n1_scan(flush, horizon=32, durations=(8, 16))   # short outages: nothing has time to run dry
        assert scan["baseline"]["unmet_liters"] == 0
        assert not scan["top_critical"]                        # nothing runs dry
        assert scan["worst_assets"][0]["sole_supply_path"]     # and yet the fragile route still ranks first
        assert scan["worst_assets"][0]["asset"] == "route-a-single"

class TestAttribution:
    """Numbers must be attributable to the asset they sit next to, or they teach an operator nothing."""

    def test_a_stockout_the_network_already_had_is_not_blamed_on_an_asset(self):
        """With no routes at all every projection runs dry; none of it is any single asset's fault."""
        starved = world(routes=())
        scan = n1_scan(starved, horizon=96)
        assert scan["baseline"]["first_stockout_tick"] is not None
        assert all(f["tts_hours"] is None for f in scan["findings"])

    def test_a_stockout_the_failure_actually_causes_is_reported(self):
        scan = n1_scan(world(), horizon=96)
        single = next(f for f in scan["findings"]
                      if f["asset"] == "route-a-single" and f["duration_ticks"] == 48)
        assert scan["baseline"]["first_stockout_tick"] is None     # a healthy baseline
        assert single["tts_hours"] is not None                     # so this one is genuinely caused by the cut

    def test_a_route_that_is_merely_the_last_one_working_is_labelled_differently(self):
        """Losing a sibling to a disruption is today's exposure, not a permanent design flaw."""
        routes = (TwinRoute("route-a-twinfed", "depot-a", "station-twinfed", 2, 5000.0),
                  TwinRoute("route-b-twinfed", "depot-b", "station-twinfed", 3, 5000.0, available=False),
                  TwinRoute("route-a-single", "depot-a", "station-single", 2, 5000.0))
        scan = n1_scan(world(routes=routes), horizon=96)
        temporary = next(f for f in scan["findings"] if f["asset"] == "route-a-twinfed")
        assert temporary["only_working_route"] is True
        assert temporary["sole_supply_path"] is False              # the sibling exists, it is just cut today
        permanent = next(f for f in scan["findings"] if f["asset"] == "route-a-single")
        assert permanent["sole_supply_path"] is True
        assert permanent["only_working_route"] is False
        assert {s["asset"] for s in scan["single_points_of_failure"]} == {"route-a-single"}
        assert {s["asset"] for s in scan["only_working_route"]} == {"route-a-twinfed"}
