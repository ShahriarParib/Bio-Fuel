"""Shared feasibility rules (docs/architecture.md §3). Every policy line and every manual line must pass `check_line`.

Rules that exist because of verified simulator behaviour (02_SIMULATOR_FACTS / PROGRESS.md):
- rule 2: a route disruption that becomes active at the departure step makes the allocation FAIL with no refund.
- rule 6: the dispatch cap is per depot per tick and shared by all fuels.
- rule 7: the simulator's capacity check ignores in-flight fuel; fuel above capacity on arrival is lost.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .planning import Inflight, PlanningState, inventory_before_arrival

MIN_LINE_LITERS = 300.0
OVERFLOW_GUARD = 0.98          # projected inventory at arrival + qty ≤ 98% of capacity
CONSTRAINED_DISPATCH_FACTOR = 0.5  # our own policy for CONSTRAINED depots (ADR-15)


@dataclass(frozen=True)
class Line:
    route_id: str
    source_depot_id: str
    destination_station_id: str
    fuel_type: str
    quantity: float
    reason: str = ""
    critical: bool = False


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str


def _event_covers(e, ids_key: str, entity_id: str, t_from: int, t_to: int) -> bool:
    """Non-resolved event whose filter includes the entity (empty filter = ALL) and overlaps [t_from, t_to]."""
    if e.status == "RESOLVED":
        return False
    ids = e.parameters.get(ids_key) or []
    if ids and entity_id not in ids:
        return False
    return e.start_tick <= t_to and e.end_tick > t_from


def route_blocked_for_departure(state: PlanningState, route_id: str) -> bool:
    """True if a shipment created now could fail at departure.

    Departure is processed in the next step; we also treat a disruption starting one tick later as blocking, because the
    order of event activation vs departure inside a step is not documented (one tick of safety margin is cheap).
    """
    route = state.routes.get(route_id)
    if route is None or route.status != "AVAILABLE":
        return True
    return any(
        e.type == "route_disruption" and _event_covers(e, "route_ids", route_id, state.tick, state.tick + 1)
        for e in state.events
    )


def station_closed_now(state: PlanningState, station_id: str) -> bool:
    st = state.stations.get(station_id)
    if st is None or st.status != "OPEN":
        return True
    return any(
        e.type == "station_outage" and e.status == "ACTIVE" and _event_covers(e, "station_ids", station_id,
                                                                                state.tick, state.tick)
        for e in state.events
    )


def dispatch_cap(state: PlanningState, depot_id: str) -> float:
    d = state.depots[depot_id]
    cap = float(d.dispatch_capacity_per_tick)
    return cap * CONSTRAINED_DISPATCH_FACTOR if d.status == "CONSTRAINED" else cap


@dataclass
class Budget:
    """Running totals while a plan is built, so line N sees the effect of lines 1..N-1."""

    state: PlanningState
    reserve_fraction: float = 0.0
    depot_left: dict[tuple[str, str], float] = field(default_factory=dict)
    dispatch_used: dict[str, float] = field(default_factory=dict)
    added: list[Inflight] = field(default_factory=list)

    def __post_init__(self) -> None:
        for d in self.state.depots.values():
            for f, inv in d.inventory.items():
                reserve = self.reserve_fraction * float(d.capacity[f])
                self.depot_left[(d.id, f)] = max(0.0, float(inv) - reserve)
            self.dispatch_used[d.id] = float(self.state.dispatched_this_tick.get(d.id, 0.0))

    def dispatch_left(self, depot_id: str) -> float:
        return max(0.0, dispatch_cap(self.state, depot_id) - self.dispatch_used.get(depot_id, 0.0))

    def arrival_tick(self, route_id: str) -> int:
        return self.state.tick + self.state.routes[route_id].transit_ticks

    def room_at_arrival(self, route_id: str, station_id: str, fuel: str) -> float:
        """Liters that fit (≤ 98% capacity) when a shipment on this route arrives, p10 demand path."""
        st = self.state.stations[station_id]
        transit = self.state.routes[route_id].transit_ticks
        key = (station_id, fuel)
        before = inventory_before_arrival(self.state, key, transit, low=True)
        arrive = self.arrival_tick(route_id)
        before += sum(i.quantity for i in self.added
                      if (i.station_id, i.fuel_type) == key and i.arrival_tick <= arrive)
        return OVERFLOW_GUARD * float(st.capacity[fuel]) - before

    def room_now(self, station_id: str, fuel: str) -> float:
        """The simulator's own check: current inventory + quantity ≤ capacity (ignores in-flight)."""
        st = self.state.stations[station_id]
        return float(st.capacity[fuel]) - float(st.inventory[fuel])

    def commit(self, line: Line) -> None:
        self.depot_left[(line.source_depot_id, line.fuel_type)] -= line.quantity
        self.dispatch_used[line.source_depot_id] = self.dispatch_used.get(line.source_depot_id, 0.0) + line.quantity
        self.added.append(Inflight(line.destination_station_id, line.fuel_type, self.arrival_tick(line.route_id),
                                   line.quantity))


def check_line(budget: Budget, line: Line) -> list[Violation]:
    """All violations of one line against the current budget (empty list = feasible)."""
    s = budget.state
    v: list[Violation] = []
    route = s.routes.get(line.route_id)
    if route is None:
        return [Violation("ROUTE_EXISTS", f"unknown route {line.route_id}")]
    if route.source_depot_id != line.source_depot_id or route.destination_station_id != line.destination_station_id:
        return [Violation("ROUTE_MATCH", "route does not connect this depot and station")]
    if line.source_depot_id not in s.depots or line.destination_station_id not in s.stations:
        return [Violation("ENTITY_EXISTS", "unknown depot or station")]
    if line.quantity <= 0:
        return [Violation("QUANTITY", "quantity must be positive")]

    if route_blocked_for_departure(s, line.route_id):
        v.append(Violation("ROUTE_SAFE_AT_DEPARTURE",
                           "route disrupted now or at departure: the shipment would fail and the fuel would be lost"))
    if s.depots[line.source_depot_id].status not in ("OPEN", "CONSTRAINED"):
        v.append(Violation("DEPOT_OPEN", "depot not open"))
    if station_closed_now(s, line.destination_station_id):
        v.append(Violation("STATION_OPEN", "station in outage: the simulator rejects shipments"))
    if line.quantity > route.max_shipment + 1e-6:
        v.append(Violation("ROUTE_MAX_SHIPMENT", f"{line.quantity:.0f} L > route max {route.max_shipment:.0f} L"))
    left = budget.depot_left.get((line.source_depot_id, line.fuel_type), 0.0)
    if line.quantity > left + 1e-6:
        v.append(Violation("DEPOT_INVENTORY", f"{line.quantity:.0f} L > {left:.0f} L available at the depot"))
    dl = budget.dispatch_left(line.source_depot_id)
    if line.quantity > dl + 1e-6:
        v.append(Violation("DISPATCH_CAPACITY", f"{line.quantity:.0f} L > {dl:.0f} L dispatch left this tick"))
    if line.quantity > budget.room_now(line.destination_station_id, line.fuel_type) + 1e-6:
        v.append(Violation("STATION_CAPACITY_NOW", "current inventory + quantity exceeds station capacity"))
    room = budget.room_at_arrival(line.route_id, line.destination_station_id, line.fuel_type)
    if line.quantity > room + 1e-6:
        v.append(Violation("OVERFLOW_AT_ARRIVAL",
                           f"only {max(room, 0):.0f} L of room expected when it arrives (in-flight included)"))
    if line.quantity < MIN_LINE_LITERS and not line.critical:
        v.append(Violation("MIN_LINE_SIZE", f"lines under {MIN_LINE_LITERS:.0f} L are noise"))
    return v
