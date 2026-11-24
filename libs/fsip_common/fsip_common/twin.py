"""A shadow twin of the fuel network, and the N-1 question it exists to answer.

Operators of any supply network ask one question before anything goes wrong: **if I lose one asset, what breaks,
and do I have time to react?** That is the N-1 criterion borrowed from power-grid planning, and it is the only
honest way to rank "which of my routes is actually fragile" before the day it matters.

Answering it needs a forward simulation we control, because the real simulator cannot be forked: we cannot ask it
"what would the next day look like if route X were cut". So this module replays the network forward from the
current state, closed-loop — our own replenishment policy reacts every tick, exactly as it would in production —
with one asset removed.

Everything here is pure: dataclasses in, numbers out, no I/O and no clock. That is what makes it testable, and it
is why the engine can run a whole N-1 scan inside one tick without touching the simulator.

What the twin deliberately does NOT claim:
* it uses the forecast, not the future, so it is an estimate and every report says so;
* it runs our simple replenishment rule, not the LP, because 25 projections per scan must fit in a tick. The LP
  does better in reality, so a "critical" verdict here is conservative;
* it inherits the simulator behaviour we measured: fuel lands at `tick + transit`, a closed station records demand
  as unmet without drawing down its tank, and the depot dispatch cap is per tick and shared across fuels.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

FUELS: tuple[str, ...] = ("DIESEL", "PETROL", "OCTANE")
Key = tuple[str, str]  # (station_id, fuel)

OVERFLOW_GUARD = 0.98      # never plan a tank above this fraction: the simulator loses the surplus on arrival
MIN_LINE = 300.0           # below this a shipment is not worth a truck
REORDER_COVER_TICKS = 24   # top a station up when its cover drops under this many ticks of demand


@dataclass(frozen=True)
class TwinStation:
    id: str
    capacity: dict[str, float]
    inventory: dict[str, float]


@dataclass(frozen=True)
class TwinDepot:
    id: str
    inventory: dict[str, float]
    dispatch_per_tick: float


@dataclass(frozen=True)
class TwinRoute:
    id: str
    depot_id: str
    station_id: str
    transit_ticks: int
    max_shipment: float
    available: bool = True


@dataclass(frozen=True)
class TwinSupply:
    tick: int
    depot_id: str
    fuel: str
    quantity: float


@dataclass(frozen=True)
class TwinWorld:
    """Everything the projection needs, as of `tick`."""
    tick: int
    tick_minutes: int
    stations: tuple[TwinStation, ...]
    depots: tuple[TwinDepot, ...]
    routes: tuple[TwinRoute, ...]
    demand: dict[Key, tuple[float, ...]]          # index j = demand during tick (tick + j)
    supply: tuple[TwinSupply, ...] = ()
    inflight: tuple[tuple[int, str, str, float], ...] = ()   # (arrival_tick, station, fuel, qty)

    @property
    def ticks_per_hour(self) -> float:
        return 60.0 / self.tick_minutes


@dataclass
class TwinResult:
    horizon: int
    unmet_liters: float
    # demand lost simply because the closed asset could not sell it. Nothing we ship anywhere prevents this, so it
    # is reported but never used to rank fragility - otherwise every station outage outranks every real weakness.
    unmet_while_closed: float
    demand_liters: float
    delivered_liters: float
    first_stockout_tick: int | None
    stockout_keys: tuple[Key, ...]
    worst_station: Key | None
    shipments: int

    @property
    def service_level(self) -> float:
        return 1.0 if self.demand_liters <= 0 else 1.0 - self.unmet_liters / self.demand_liters


def _demand_at(world: TwinWorld, key: Key, j: int) -> float:
    series = world.demand.get(key) or ()
    if not series:
        return 0.0
    return float(series[j]) if j < len(series) else float(series[-1])


def project(world: TwinWorld, horizon: int, blocked: Iterable[str] = (), block_ticks: int | None = None,
            reorder_cover_ticks: int = REORDER_COVER_TICKS) -> TwinResult:
    """Run the network forward `horizon` ticks with our replenishment policy reacting every tick.

    `blocked` names assets (route, depot or station ids) that are unavailable for the first `block_ticks` ticks
    (all of them when `block_ticks` is None). A blocked station keeps generating demand that is recorded as unmet,
    which is what the simulator does during a station outage.
    """
    out = {a for a in blocked}
    block_until = horizon if block_ticks is None else min(block_ticks, horizon)

    def down(asset: str, j: int) -> bool:
        return asset in out and j < block_until

    inv = {(s.id, f): float(s.inventory.get(f, 0.0)) for s in world.stations for f in FUELS}
    cap = {(s.id, f): float(s.capacity.get(f, 0.0)) for s in world.stations for f in FUELS}
    depot_inv = {(d.id, f): float(d.inventory.get(f, 0.0)) for d in world.depots for f in FUELS}
    depot_cap = {d.id: float(d.dispatch_per_tick) for d in world.depots}
    routes_to: dict[str, list[TwinRoute]] = {}
    for r in world.routes:
        routes_to.setdefault(r.station_id, []).append(r)

    arrivals: dict[int, list[tuple[str, str, float]]] = {}
    for at, sid, f, qty in world.inflight:
        arrivals.setdefault(at - world.tick, []).append((sid, f, float(qty)))
    supply_at: dict[int, list[TwinSupply]] = {}
    for s in world.supply:
        supply_at.setdefault(s.tick - world.tick, []).append(s)

    unmet = unmet_closed = demand_total = delivered = 0.0
    first_stockout: int | None = None
    stockouts: set[Key] = set()
    worst: dict[Key, float] = {}
    shipments = 0

    for j in range(horizon):
        # 1. arrivals land first: fuel added during this step is available to this step's demand
        for sid, f, qty in arrivals.pop(j, []):
            room = max(0.0, cap[(sid, f)] - inv[(sid, f)])
            inv[(sid, f)] += min(qty, room)          # the surplus is lost, exactly as the simulator loses it
            delivered += min(qty, room)
        for sup in supply_at.pop(j, []):
            depot_inv[(sup.depot_id, sup.fuel)] = depot_inv.get((sup.depot_id, sup.fuel), 0.0) + sup.quantity

        # 2. demand is consumed
        for s in world.stations:
            for f in FUELS:
                key = (s.id, f)
                d = _demand_at(world, key, j)
                if d <= 0:
                    continue
                demand_total += d
                if down(s.id, j):
                    # a closed station sells nothing: the demand is recorded as unmet, the tank does not fall
                    unmet += d
                    unmet_closed += d
                    continue
                served = min(d, inv[key])
                inv[key] -= served
                if served < d:
                    short = d - served
                    unmet += short
                    worst[key] = worst.get(key, 0.0) + short
                    if key not in stockouts:
                        stockouts.add(key)
                        if first_stockout is None:
                            first_stockout = j

        # 3. our policy reacts: top up whatever will not last `reorder_cover_ticks`
        left = dict(depot_cap)
        for s in world.stations:
            if down(s.id, j):
                continue
            for f in FUELS:
                key = (s.id, f)
                options = [r for r in routes_to.get(s.id, [])
                           if r.available and not down(r.id, j) and not down(r.depot_id, j)]
                if not options:
                    continue
                options.sort(key=lambda r: (r.transit_ticks, -r.max_shipment))
                for r in options:
                    lead = r.transit_ticks
                    coming = sum(q for at, loads in arrivals.items() if j < at <= j + lead
                                 for sid, ff, q in loads if sid == s.id and ff == f)
                    at_arrival = inv[key] + coming - sum(_demand_at(world, key, k) for k in range(j, j + lead))
                    need_window = range(j, j + lead + reorder_cover_ticks)
                    need = sum(_demand_at(world, key, k) for k in need_window) - max(0.0, at_arrival)
                    if need < MIN_LINE:
                        break                                   # this station is fine on its fastest route
                    room = cap[key] * OVERFLOW_GUARD - max(0.0, at_arrival)
                    qty = min(need, room, r.max_shipment, depot_inv.get((r.depot_id, f), 0.0),
                              left.get(r.depot_id, 0.0))
                    if qty < MIN_LINE:
                        continue                                # try the next route (another depot may have stock)
                    depot_inv[(r.depot_id, f)] -= qty
                    left[r.depot_id] = left.get(r.depot_id, 0.0) - qty
                    arrivals.setdefault(j + lead, []).append((s.id, f, qty))
                    shipments += 1
                    break

    worst_key = max(worst, key=lambda k: worst[k]) if worst else None
    return TwinResult(horizon=horizon, unmet_liters=round(unmet, 1), unmet_while_closed=round(unmet_closed, 1),
                      demand_liters=round(demand_total, 1),
                      delivered_liters=round(delivered, 1),
                      first_stockout_tick=None if first_stockout is None else world.tick + first_stockout,
                      stockout_keys=tuple(sorted(stockouts)), worst_station=worst_key, shipments=shipments)


# --------------------------------------------------------------------------- N-1 contingency
@dataclass
class Contingency:
    asset: str
    kind: str                 # route | depot | station
    label: str
    duration_ticks: int
    unmet_liters: float
    extra_unmet_vs_normal: float      # what the failure costs on top of a normal day, excluding unavoidable loss
    service_level: float
    first_stockout_tick: int | None
    tts_hours: float | None   # time to the first stockout if this asset goes now
    ttr_hours: float | None   # time to restore: how fast fuel could reach the worst station another way
    critical: bool            # the network runs dry before an alternative can arrive
    worst_station: str | None
    # A sole supply path: the station this asset feeds has no other route at all. This is a property of the
    # network, not of today's fuel level, so it stays true (and worth acting on) even when nothing runs dry.
    sole_supply_path: bool = False        # the station has no other route at all, today or any day
    only_working_route: bool = False      # other routes exist but every one of them is disrupted right now
    serves: str | None = None
    unmet_while_closed: float = 0.0

    def as_dict(self) -> dict:
        return {
            "asset": self.asset, "kind": self.kind, "label": self.label,
            "duration_ticks": self.duration_ticks, "unmet_liters": round(self.unmet_liters),
            "extra_unmet_vs_normal": round(self.extra_unmet_vs_normal),
            "unavoidable_unmet": round(self.unmet_while_closed),
            "service_level": round(self.service_level, 4),
            "first_stockout_tick": self.first_stockout_tick,
            "tts_hours": None if self.tts_hours is None else round(self.tts_hours, 1),
            "ttr_hours": None if self.ttr_hours is None else round(self.ttr_hours, 1),
            "critical": self.critical, "worst_station": self.worst_station,
            "sole_supply_path": self.sole_supply_path, "only_working_route": self.only_working_route,
            "serves": self.serves,
        }


def _served_station(world: TwinWorld, asset: str, kind: str) -> str | None:
    """The station whose supply this asset is part of, so a redundant route can still report its reroute time."""
    if kind == "station":
        return asset
    if kind == "route":
        return next((r.station_id for r in world.routes if r.id == asset), None)
    return None


def _sole_path_flags(world: TwinWorld, asset: str, kind: str) -> tuple[bool, bool]:
    """(structurally sole, sole only because something else is disrupted right now).

    The distinction matters to an operator. "This station has one route and always will" is a reason to pre-stock
    it permanently. "This is its only *working* route today" is a reason to act this shift and no longer. Reporting
    both as the same thing would turn a temporary disruption into a permanent-looking design flaw.
    """
    station = _served_station(world, asset, kind)
    if station is None or kind == "station":
        return False, False
    siblings = [r for r in world.routes
                if r.station_id == station and r.id != asset and r.depot_id != asset]
    if not siblings:
        return True, False
    return False, not any(r.available for r in siblings)


def _ttr_hours(world: TwinWorld, asset: str, station_id: str | None) -> float | None:
    """How long until fuel could reach `station_id` without `asset`: the best remaining route, plus one tick to plan."""
    if station_id is None:
        return None
    options = [r for r in world.routes
               if r.station_id == station_id and r.available and r.id != asset and r.depot_id != asset]
    if not options:
        return None
    best = min(r.transit_ticks for r in options)
    return (best + 1) / world.ticks_per_hour


def assets_of(world: TwinWorld, names: dict[str, str] | None = None) -> list[tuple[str, str, str]]:
    """Every single asset whose loss is worth asking about, as (id, kind, label)."""
    names = names or {}
    out: list[tuple[str, str, str]] = []
    for r in world.routes:
        out.append((r.id, "route", names.get(r.id) or r.id.removeprefix("route-")))
    for d in world.depots:
        out.append((d.id, "depot", names.get(d.id) or d.id.removeprefix("depot-")))
    for s in world.stations:
        out.append((s.id, "station", names.get(s.id) or s.id.removeprefix("station-")))
    return out


def n1_scan(world: TwinWorld, horizon: int = 96, durations: tuple[int, ...] = (24, 48),
            names: dict[str, str] | None = None) -> dict:
    """Lose each asset in turn, for each duration, and report what it costs.

    The baseline (nothing lost) is projected once; every contingency is measured against it, so the number an
    operator reads is "what this failure would cost on top of a normal day", not an absolute that mixes in the
    network's existing strain.
    """
    baseline = project(world, horizon)
    findings: list[Contingency] = []
    for asset, kind, label in assets_of(world, names):
        for duration in durations:
            res = project(world, horizon, blocked=(asset,), block_ticks=duration)
            worst = res.worst_station[0] if res.worst_station else None
            avoidable = max(0.0, (res.unmet_liters - res.unmet_while_closed) - baseline.unmet_liters)
            # a stockout the baseline already has is not this asset's doing; reporting it against every asset
            # would make the whole column read the same and tell an operator nothing
            caused = (res.first_stockout_tick is not None
                      and (baseline.first_stockout_tick is None
                           or res.first_stockout_tick < baseline.first_stockout_tick))
            tts = ((res.first_stockout_tick - world.tick) / world.ticks_per_hour) if caused else None
            ttr = _ttr_hours(world, asset, worst or _served_station(world, asset, kind))
            serves = _served_station(world, asset, kind)
            sole, only_working = _sole_path_flags(world, asset, kind)
            findings.append(Contingency(
                asset=asset, kind=kind, label=label, duration_ticks=duration,
                sole_supply_path=sole, only_working_route=only_working, serves=serves,
                unmet_liters=res.unmet_liters,
                extra_unmet_vs_normal=avoidable,
                service_level=res.service_level, first_stockout_tick=res.first_stockout_tick,
                tts_hours=tts, ttr_hours=ttr,
                unmet_while_closed=res.unmet_while_closed,
                critical=bool(tts is not None and (ttr is None or tts < ttr)),
                worst_station=worst,
            ))
    # Rank by what an operator should act on first: a failure that actually runs the network dry, then a sole
    # supply path (true whatever today's inventory says), then the sheer litres at stake.
    ranked = sorted(findings, key=lambda c: (not c.critical, not (c.sole_supply_path or c.only_working_route),
                                             -c.extra_unmet_vs_normal, c.tts_hours or 1e9))
    return {
        "tick": world.tick,
        "horizon_ticks": horizon,
        "durations": list(durations),
        "method": ("closed-loop shadow projection: our replenishment policy reacts every tick while the asset is "
                   "unavailable; demand is the p50 forecast, so these are estimates, not predictions"),
        "baseline": {"unmet_liters": round(baseline.unmet_liters),
                     "first_stockout_tick_note": "stockouts at or after this tick are not attributed to any asset",
                     "service_level": round(baseline.service_level, 4),
                     "first_stockout_tick": baseline.first_stockout_tick},
        "findings": [c.as_dict() for c in findings],
        "top_critical": [c.as_dict() for c in ranked if c.critical][:3],
        "worst_assets": [c.as_dict() for c in ranked][:3],
        "single_points_of_failure": [
            {"asset": c.asset, "label": c.label, "serves": c.serves, "kind": c.kind}
            for c in {c.asset: c for c in findings if c.sole_supply_path}.values()],
        "only_working_route": [
            {"asset": c.asset, "label": c.label, "serves": c.serves, "kind": c.kind}
            for c in {c.asset: c for c in findings if c.only_working_route}.values()],
    }


def world_with(world: TwinWorld, **changes) -> TwinWorld:
    """Small helper for tests and what-ifs."""
    return replace(world, **changes)


__all__ = [
    "Contingency", "TwinDepot", "TwinResult", "TwinRoute", "TwinStation", "TwinSupply", "TwinWorld",
    "assets_of", "n1_scan", "project", "world_with",
]
