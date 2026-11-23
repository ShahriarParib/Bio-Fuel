"""Monte Carlo stockout risk and multiplier reconstruction."""
from __future__ import annotations

import numpy as np
from app.intelligence.series import base_multiplier, future_windows, multiplier_at
from app.risk.stockout import level_for, simulate
from fsip_common.models import SimEvent, Station

H = 48
P50 = np.full(H, 100.0)
P10 = P50 * 0.9
P90 = P50 * 1.1
NO_OUTAGE = np.zeros(H, dtype=bool)


def prob(inv, arrivals=None, outage=NO_OUTAGE):
    first, _ = simulate(inv, 20_000, P10, P50, P90, np.zeros(H) if arrivals is None else arrivals, outage, seed=7)
    return float((first < H).mean())


def test_more_inventory_never_increases_risk():
    probs = [prob(inv) for inv in (1000, 2500, 4000, 5000, 8000)]
    assert probs == sorted(probs, reverse=True)
    assert probs[0] > 0.95 and probs[-1] < 0.01


def test_a_timely_arrival_cuts_risk():
    arrivals = np.zeros(H)
    arrivals[10] = 5000
    assert prob(2000) > 0.9
    assert prob(2000, arrivals) < 0.1


def test_outage_ticks_consume_nothing():
    outage = np.zeros(H, dtype=bool)
    outage[:40] = True
    assert prob(1000, outage=outage) < prob(1000)


def test_levels():
    assert level_for(0.1, 0.6, None, 1.0) == "CRITICAL"
    assert level_for(0.6, 0.1, 8.0, 1.0) == "HIGH"
    assert level_for(0.3, 0.0, None, 1.0) == "MEDIUM"
    assert level_for(0.05, 0.0, None, 1.0) == "LOW"


def _station(mult=1.0):
    return Station(id="station-mirpur", name="Mirpur", region_id="region-dhaka", status="OPEN",
                   demand_profile="urban_high", demand_multiplier=mult,
                   capacity={"DIESEL": 1, "PETROL": 1, "OCTANE": 1}, inventory={"DIESEL": 0, "PETROL": 0, "OCTANE": 0})


def _spike(eid, start, end, status, m=1.8, **params):
    return SimEvent(id=eid, type="demand_spike", start_tick=start, end_tick=end, status=status,
                    parameters={"multiplier": m, **params})


def test_past_multiplier_is_reconstructed_from_events():
    events = [_spike(1, 10, 20, "RESOLVED", region_ids=["region-dhaka"]),
              _spike(2, 30, 40, "ACTIVE", m=1.5, station_ids=["station-mirpur"])]
    st = _station(mult=1.5)  # currently inside event 2
    base = base_multiplier(st, events)
    assert base == 1.0
    assert multiplier_at(st, events, 15, base) == 1.8
    assert multiplier_at(st, events, 25, base) == 1.0
    assert multiplier_at(st, events, 35, base) == 1.5


def test_future_windows_end_active_and_start_scheduled_spikes():
    events = [_spike(2, 30, 40, "ACTIVE", m=1.5), _spike(3, 50, 60, "SCHEDULED", m=2.0, region_ids=["region-x"])]
    wins = future_windows(_station(mult=1.5), events)
    assert len(wins) == 1 and wins[0].from_tick == 40 and abs(wins[0].multiplier - 1 / 1.5) < 1e-9
