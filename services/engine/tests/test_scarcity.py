"""Scarcity detection and fairness measurement.

The questions behind these tests: does the banner stay still when the forecast wobbles, and does the fairness
number actually notice one station being starved while the headline service level stays high?
"""
from __future__ import annotations

from dataclasses import dataclass

from app.risk.scarcity import (
    ENTER_CYCLES,
    EXIT_CYCLES,
    ScarcityTracker,
    cover_ratios,
    diagnose,
    fairness,
    jain_index,
    station_service_levels,
    supply_ends_tick,
)
from fsip_common.models import SupplyArrival
from test_decision import world as fixture_world


@dataclass
class Obs:
    """A demand-history row, as the simulator reports it."""
    tick: int
    demand_liters: float
    served_liters: float
    unmet_liters: float = 0.0


def rows(tick_from: int, tick_to: int, demand: float, served: float) -> list[Obs]:
    return [Obs(t, demand, served, demand - served) for t in range(tick_from, tick_to)]


class TestJainIndex:
    def test_everyone_served_equally_is_perfectly_fair(self):
        assert jain_index([1.0, 1.0, 1.0, 1.0]) == 1.0
        assert jain_index([0.5, 0.5, 0.5]) == 1.0      # equally badly served is still equal

    def test_one_station_taking_everything_is_the_worst_case(self):
        assert jain_index([1.0, 0.0, 0.0, 0.0]) == round(1 / 4, 4)

    def test_it_notices_a_single_starved_station(self):
        even = jain_index([1.0, 1.0, 1.0, 1.0])
        starved = jain_index([1.0, 1.0, 1.0, 0.2])
        assert starved < even

    def test_it_judges_the_shape_not_the_scale(self):
        """Halving everyone's service level is a catastrophe, but it is not an unfairness."""
        assert jain_index([0.8, 0.6, 0.4]) == jain_index([0.4, 0.3, 0.2])

    def test_no_stations_is_not_a_number(self):
        assert jain_index([]) is None


class TestStationServiceLevels:
    def test_it_measures_each_station_on_its_own_demand(self):
        demand = {
            ("station-a", "DIESEL"): rows(0, 10, 100, 100),     # fully served
            ("station-b", "DIESEL"): rows(0, 10, 100, 50),      # half served
        }
        per = station_service_levels(demand, since_tick=0)
        assert per == {"station-a": 1.0, "station-b": 0.5}

    def test_a_station_with_several_fuels_is_judged_as_one_station(self):
        demand = {
            ("station-a", "DIESEL"): rows(0, 10, 100, 100),
            ("station-a", "PETROL"): rows(0, 10, 100, 0),
        }
        assert station_service_levels(demand, since_tick=0) == {"station-a": 0.5}

    def test_history_before_the_window_is_ignored(self):
        demand = {("station-a", "DIESEL"): rows(0, 10, 100, 0) + rows(10, 20, 100, 100)}
        assert station_service_levels(demand, since_tick=10) == {"station-a": 1.0}

    def test_a_station_with_no_demand_is_not_counted_as_failing(self):
        assert station_service_levels({("station-a", "DIESEL"): rows(0, 10, 0, 0)}, since_tick=0) == {"station-a": 1.0}


class TestFairnessReport:
    def test_a_high_headline_can_still_hide_a_starved_station(self):
        """This is the whole reason the module exists."""
        demand = {
            ("station-a", "DIESEL"): rows(0, 96, 1000, 1000),
            ("station-b", "DIESEL"): rows(0, 96, 1000, 1000),
            ("station-c", "DIESEL"): rows(0, 96, 1000, 1000),
            ("station-d", "DIESEL"): rows(0, 96, 100, 10),        # tiny station, almost nothing served
        }
        f = fairness(demand, tick=96)
        network_sl = (3 * 96 * 1000 + 96 * 10) / (3 * 96 * 1000 + 96 * 100)
        assert network_sl > 0.97                                   # the headline looks fine
        assert f["min_station"] == 0.1                             # one station is not
        assert f["worst_station"] == "station-d"
        assert f["jain"] < 0.9

    def test_an_equally_served_network_scores_one(self):
        demand = {(f"station-{n}", "DIESEL"): rows(0, 96, 500, 500) for n in "abcd"}
        f = fairness(demand, tick=96)
        assert f["jain"] == 1.0 and f["min_station"] == 1.0 and f["spread"] == 0.0

    def test_with_no_history_it_says_so_instead_of_guessing(self):
        f = fairness({}, tick=10)
        assert f["jain"] is None and f["per_station"] == {}


class TestHysteresis:
    def test_one_bad_cycle_does_not_declare_scarcity(self):
        t = ScarcityTracker()
        assert t.update({"DIESEL": 0.8, "PETROL": 2.0, "OCTANE": 2.0}) == []
        assert t.active["DIESEL"] is False

    def test_scarcity_is_declared_only_after_consecutive_bad_cycles(self):
        t = ScarcityTracker()
        for _ in range(ENTER_CYCLES - 1):
            t.update({"DIESEL": 0.8})
        changes = t.update({"DIESEL": 0.8})
        assert ("DIESEL", "entered") in changes
        assert t.active["DIESEL"] is True

    def test_a_ratio_in_the_gap_resets_the_count_without_flipping_the_state(self):
        """Between 1.0 and 1.1 nothing is counted: that gap is what stops the banner blinking."""
        t = ScarcityTracker()
        t.update({"DIESEL": 0.8})
        t.update({"DIESEL": 1.05})          # in the dead band
        t.update({"DIESEL": 0.8})           # the earlier bad cycle no longer counts
        assert t.active["DIESEL"] is False

    def test_leaving_scarcity_needs_more_evidence_than_entering_it(self):
        t = ScarcityTracker()
        for _ in range(ENTER_CYCLES):
            t.update({"DIESEL": 0.8})
        assert t.active["DIESEL"] is True
        for _ in range(EXIT_CYCLES - 1):
            t.update({"DIESEL": 1.5})
        assert t.active["DIESEL"] is True                     # still not convinced
        assert ("DIESEL", "exited") in t.update({"DIESEL": 1.5})
        assert t.active["DIESEL"] is False

    def test_each_fuel_is_tracked_separately(self):
        t = ScarcityTracker()
        for _ in range(ENTER_CYCLES):
            t.update({"DIESEL": 0.5, "PETROL": 3.0, "OCTANE": 3.0})
        assert t.active["DIESEL"] is True
        assert t.active["PETROL"] is False

    def test_a_missing_ratio_leaves_the_state_alone(self):
        t = ScarcityTracker()
        for _ in range(ENTER_CYCLES):
            t.update({"DIESEL": 0.5})
        assert t.update({"DIESEL": None}) == []
        assert t.active["DIESEL"] is True


class TestCoverRatios:
    def test_a_well_stocked_network_covers_its_horizon(self):
        ratios = cover_ratios(fixture_world(demand=10.0))
        assert all(v is None or v > 1.0 for v in ratios["network"].values())

    def test_heavy_demand_drives_the_ratio_under_one(self):
        ratios = cover_ratios(fixture_world(demand=5000.0))
        assert any(v is not None and v < 1.0 for v in ratios["network"].values())

    def test_every_region_is_reported(self):
        st = fixture_world()
        ratios = cover_ratios(st)
        assert set(ratios["regions"]) >= {s.region_id for s in st.stations.values()}

    def test_scheduled_supply_counts_as_deliverable(self):
        st = fixture_world(demand=600.0)
        before = cover_ratios(st)["network"]["DIESEL"]
        depot_id = next(iter(st.depots))
        st.supply = [SupplyArrival(id="sup-1", depot_id=depot_id, fuel_type="DIESEL", quantity=500_000.0,
                                   planned_tick=st.tick + 10, status="SCHEDULED")]
        assert cover_ratios(st)["network"]["DIESEL"] > before

    def test_when_nothing_is_scheduled_the_network_is_living_off_stock(self):
        st = fixture_world()
        st.supply = []
        assert supply_ends_tick(st) is None

class TestDiagnosis:
    """Two very different problems look identical on a stockout chart and need opposite responses.

    We learned this from our own stack: every station bone dry while the depots still held 67,000 L of diesel.
    Nothing was scarce - the platform had been left in advisory mode and nobody had approved anything.
    """

    def test_not_enough_fuel_is_a_supply_problem(self):
        d = diagnose({"DIESEL": 0.6, "PETROL": 1.4, "OCTANE": 1.4}, min_station=0.4)
        assert d["constraint"] == "supply"

    def test_plenty_of_fuel_and_a_starving_station_is_a_distribution_problem(self):
        d = diagnose({"DIESEL": 2.7, "PETROL": 1.8, "OCTANE": 2.5}, min_station=0.0, mode="autopilot")
        assert d["constraint"] == "distribution"
        assert "dispatch caps" in d["detail"]

    def test_waiting_on_a_human_is_named_as_such(self):
        d = diagnose({"DIESEL": 2.7}, min_station=0.0, pending_approvals=3, mode="advisory")
        assert d["constraint"] == "approval"
        assert "3 recommendation(s)" in d["detail"]

    def test_advisory_mode_with_nothing_pending_is_not_blamed_on_approvals(self):
        d = diagnose({"DIESEL": 2.7}, min_station=0.0, pending_approvals=0, mode="advisory")
        assert d["constraint"] == "distribution"

    def test_a_healthy_network_is_not_given_a_problem_to_solve(self):
        assert diagnose({"DIESEL": 2.0}, min_station=1.0)["constraint"] == "none"

    def test_supply_outranks_distribution_when_both_are_true(self):
        """If there genuinely is not enough fuel, routing advice would be a distraction."""
        d = diagnose({"DIESEL": 0.5}, min_station=0.0, pending_approvals=5, mode="advisory")
        assert d["constraint"] == "supply"
