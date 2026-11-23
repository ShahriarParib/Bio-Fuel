"""Pure planning view of the world + projection maths (no I/O, unit-testable).

Timing semantics (verified on the image, PROGRESS.md):
- An allocation created at instance tick t with transit k departs during step t and its fuel is added to the station
  during step t+k (instance tick becomes t+k+1, `actual_arrival_tick` = t+k).
- Demand of tick j is consumed during step j. So, before our arrival, the station consumes demand of ticks t..t+k-1.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from fsip_common.models import Depot, Region, Route, SimEvent, Station, SupplyArrival

FUELS: tuple[str, ...] = ("DIESEL", "PETROL", "OCTANE")
Key = tuple[str, str]  # (station_id, fuel)


@dataclass(frozen=True)
class Inflight:
    station_id: str
    fuel_type: str
    arrival_tick: int  # tick during whose step the fuel is added
    quantity: float


@dataclass
class PlanningState:
    tick: int
    tick_minutes: int
    stations: dict[str, Station]
    depots: dict[str, Depot]
    routes: dict[str, Route]
    regions: dict[str, Region]
    events: list[SimEvent]
    supply: list[SupplyArrival]
    inflight: list[Inflight]
    # demand forecast per (station, fuel); index j = demand during tick (tick + j)
    demand_p50: dict[Key, list[float]]
    demand_p10: dict[Key, list[float]]
    # liters already dispatched from each depot in the current tick (our PENDING allocations created this tick)
    dispatched_this_tick: dict[str, float] = field(default_factory=dict)
    stale: bool = False
    degraded: bool = False
    forecast_model: str = "naive"

    @property
    def ticks_per_hour(self) -> float:
        return 60.0 / self.tick_minutes

    def demand(self, key: Key, j: int, low: bool = False) -> float:
        series = (self.demand_p10 if low else self.demand_p50).get(key) or []
        if not series:
            return 0.0
        return series[j] if j < len(series) else series[-1]

    def inflight_for(self, key: Key) -> list[Inflight]:
        return [i for i in self.inflight if (i.station_id, i.fuel_type) == key]


def inflight_from_allocations(allocations: Iterable, routes: dict[str, Route], tick: int) -> list[Inflight]:
    """Our shipments that have not reached the station yet."""
    out: list[Inflight] = []
    for a in allocations:
        if a.status == "IN_TRANSIT" and a.expected_arrival_tick is not None:
            out.append(Inflight(a.destination_station_id, a.fuel_type, a.expected_arrival_tick, a.quantity))
        elif a.status == "PENDING":
            r = routes.get(a.route_id)
            transit = r.transit_ticks if r else 2
            out.append(Inflight(a.destination_station_id, a.fuel_type, a.created_tick + transit, a.quantity))
    return out


def projected_inventory(state: PlanningState, key: Key, horizon: int, low: bool = False,
                        extra: Iterable[Inflight] = ()) -> list[float]:
    """Inventory at the START of each tick t..t+horizon (index 0 = now), clamped at 0 (shortfall = unmet).

    `low=True` uses the p10 demand path: the pessimistic case for overflow (demand turns out lower).
    """
    station = state.stations[key[0]]
    inv = float(station.inventory[key[1]])
    arrivals: dict[int, float] = {}
    for i in list(state.inflight_for(key)) + [e for e in extra if (e.station_id, e.fuel_type) == key]:
        arrivals[i.arrival_tick] = arrivals.get(i.arrival_tick, 0.0) + i.quantity
    out = [inv]
    cap = float(station.capacity[key[1]])
    for j in range(horizon):
        t = state.tick + j
        inv = min(cap, inv + arrivals.get(t, 0.0))
        inv = max(0.0, inv - state.demand(key, j, low=low))
        out.append(inv)
    return out


def inventory_before_arrival(state: PlanningState, key: Key, transit: int, low: bool = True) -> float:
    """Station inventory right before a shipment created now (transit k) is added.

    Demand of ticks t..t+k-1 is consumed first; every in-flight arrival up to and including t+k is counted (worst case
    for overflow). Uses p10 demand by default because overflow happens when demand turns out low.
    """
    path = projected_inventory(state, key, transit, low=low)
    inv = path[transit]
    arrivals_same_tick = sum(i.quantity for i in state.inflight_for(key) if i.arrival_tick == state.tick + transit)
    return inv + arrivals_same_tick


def hours_to_stockout(state: PlanningState, key: Key, horizon: int = 96) -> float | None:
    """Hours until projected (p50) inventory hits zero; None if it does not within the horizon."""
    path = projected_inventory(state, key, horizon)
    for j in range(1, len(path)):
        if path[j] <= 0.0 and state.demand(key, j - 1) > 0:
            return round((j - 1) / state.ticks_per_hour, 2)
    return None


def naive_forecast(history: dict[Key, list[float]], stations: dict[str, Station], horizon: int,
                   low_factor: float = 0.8) -> tuple[dict[Key, list[float]], dict[Key, list[float]]]:
    """Flat forecast = mean of the last 8 observed demand values (B2 placeholder; B3 replaces it).

    No history yet → 0.4% of capacity per tick (≈ observed order of magnitude).
    """
    p50: dict[Key, list[float]] = {}
    p10: dict[Key, list[float]] = {}
    for sid, st in stations.items():
        for f in FUELS:
            vals = history.get((sid, f), [])[-8:]
            level = sum(vals) / len(vals) if vals else float(st.capacity[f]) * 0.004
            p50[(sid, f)] = [level] * horizon
            p10[(sid, f)] = [level * low_factor] * horizon
    return p50, p10
