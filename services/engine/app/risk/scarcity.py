"""Scarcity and fairness: what to do when there is not enough fuel for everyone.

Most of this platform optimises. This module exists for the hours when optimising is beside the point, because the
depots have run out of scheduled supply and the only remaining question is **who goes short, and by how much**.

Two things are computed here:

1. **The fuel budget.** Everything we can still deliver (depot stock + station stock + in-flight + scheduled
   arrivals) against the demand we forecast over the next 96 ticks, per fuel and per region. The ratio is a cover
   ratio: below 1.0 means the network cannot serve the next day no matter how well we route.

2. **Fairness.** When there is not enough, "99% service level" can hide one station sitting dry for a day while
   the others are full. So we measure the *distribution*: each station's own service level over the recent window,
   the worst of them, and the Jain index - the standard fairness measure, 1.0 when everyone is served equally and
   1/n when one station takes everything.

Entering scarcity is deliberately sticky (hysteresis). A cover ratio bouncing around 1.0 would otherwise flip the
network between policies every cycle, and an operator cannot act on a banner that blinks.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from prometheus_client import Gauge

from ..decision.planning import FUELS, PlanningState

SCARCITY_ON = Gauge("fsip_scarcity_active", "1 while the network cannot cover forecast demand", ["fuel"])
COVER_RATIO = Gauge("fsip_cover_ratio", "Deliverable fuel ÷ forecast demand over the horizon", ["fuel"])
JAIN = Gauge("fsip_fairness_jain", "Jain fairness index of per-station service level (1.0 = everyone equal)")
MIN_STATION_SL = Gauge("fsip_min_station_service_level", "Service level of the worst-served station")

HORIZON_TICKS = 96
ENTER_BELOW = 1.0      # cover ratio under which the network cannot serve the horizon
EXIT_ABOVE = 1.1       # and the clearly-comfortable level it must reach again
ENTER_CYCLES = 2       # ... for this many consecutive cycles, so a single noisy forecast cannot trip it
EXIT_CYCLES = 4        # ... and this many to leave, because leaving too early is the expensive mistake


# --------------------------------------------------------------------------- the budget
def cover_ratios(state: PlanningState, horizon: int = HORIZON_TICKS) -> dict[str, dict[str, float | None]]:
    """Deliverable fuel ÷ forecast demand, per fuel, network-wide and per region.

    "Deliverable" counts fuel that still has a path to a customer: depot stock, station stock, what is already in
    transit, and supply the simulator has scheduled but not yet delivered. Fuel in a depot with no open route is
    still counted - the N-1 scan is where routing fragility is answered, not here.
    """
    out: dict[str, dict[str, float | None]] = {"network": {}, "regions": {}}
    region_of = {s.id: s.region_id for s in state.stations.values()}
    depot_region = {d.id: d.region_id for d in state.depots.values()}

    for f in FUELS:
        available = (
            sum(float(d.inventory[f]) for d in state.depots.values())
            + sum(float(s.inventory[f]) for s in state.stations.values())
            + sum(i.quantity for i in state.inflight if i.fuel_type == f)
            + sum(float(a.quantity) for a in state.supply if a.fuel_type == f and a.status != "ARRIVED")
        )
        demand = sum(state.demand((sid, f), j) for sid in state.stations for j in range(horizon))
        out["network"][f] = round(available / demand, 3) if demand > 0 else None

    for region in {*region_of.values(), *depot_region.values()}:
        per_fuel: dict[str, float | None] = {}
        for f in FUELS:
            available = (
                sum(float(d.inventory[f]) for d in state.depots.values() if d.region_id == region)
                + sum(float(s.inventory[f]) for s in state.stations.values() if s.region_id == region)
                + sum(i.quantity for i in state.inflight
                      if i.fuel_type == f and region_of.get(i.station_id) == region)
                + sum(float(a.quantity) for a in state.supply
                      if a.fuel_type == f and a.status != "ARRIVED" and depot_region.get(a.depot_id) == region)
            )
            demand = sum(state.demand((sid, f), j) for sid, s in state.stations.items()
                         if s.region_id == region for j in range(horizon))
            per_fuel[f] = round(available / demand, 3) if demand > 0 else None
        out["regions"][region] = per_fuel
    return out


def supply_ends_tick(state: PlanningState) -> int | None:
    """The last scheduled supply arrival. After it, the network is living off what it already holds."""
    pending = [a.planned_tick for a in state.supply if a.status != "ARRIVED"]
    return max(pending) if pending else None


# --------------------------------------------------------------------------- hysteresis
@dataclass
class ScarcityTracker:
    """Per-fuel scarcity state with hysteresis, so the banner an operator sees does not blink."""
    active: dict[str, bool] = field(default_factory=lambda: {f: False for f in FUELS})
    _below: dict[str, int] = field(default_factory=lambda: {f: 0 for f in FUELS})
    _above: dict[str, int] = field(default_factory=lambda: {f: 0 for f in FUELS})

    def update(self, ratios: Mapping[str, float | None]) -> list[tuple[str, str]]:
        """Feed one cycle's cover ratios. Returns the (fuel, "entered"|"exited") transitions that just happened."""
        changes: list[tuple[str, str]] = []
        for f in FUELS:
            ratio = ratios.get(f)
            if ratio is None:
                continue
            COVER_RATIO.labels(fuel=f).set(ratio)
            if ratio < ENTER_BELOW:
                self._below[f] += 1
                self._above[f] = 0
            elif ratio > EXIT_ABOVE:
                self._above[f] += 1
                self._below[f] = 0
            else:
                # between the two thresholds nothing is counted: that gap is the hysteresis
                self._below[f] = self._above[f] = 0

            if not self.active[f] and self._below[f] >= ENTER_CYCLES:
                self.active[f] = True
                changes.append((f, "entered"))
            elif self.active[f] and self._above[f] >= EXIT_CYCLES:
                self.active[f] = False
                changes.append((f, "exited"))
            SCARCITY_ON.labels(fuel=f).set(1 if self.active[f] else 0)
        return changes


# --------------------------------------------------------------------------- fairness
def jain_index(values: Iterable[float]) -> float | None:
    """Jain's fairness index: (Σx)² / (n·Σx²).

    1.0 when every station is served equally well; 1/n when one station takes everything. It is the standard
    measure in resource allocation precisely because it is scale-invariant: it judges the *shape* of the
    distribution, not how much fuel there was.
    """
    xs = [float(v) for v in values]
    if not xs:
        return None
    total = sum(xs)
    sq = sum(v * v for v in xs)
    if sq <= 0:
        return 1.0 if total == 0 else None
    return round((total * total) / (len(xs) * sq), 4)


def station_service_levels(demand: Mapping[tuple[str, str], Iterable[Any]], since_tick: int) -> dict[str, float]:
    """Each station's own service level over the window: served ÷ demanded, across all its fuels."""
    served: dict[str, float] = {}
    wanted: dict[str, float] = {}
    for (sid, _fuel), rows in demand.items():
        for o in rows:
            if o.tick < since_tick:
                continue
            wanted[sid] = wanted.get(sid, 0.0) + float(o.demand_liters)
            served[sid] = served.get(sid, 0.0) + float(o.served_liters)
    return {sid: round(served.get(sid, 0.0) / d, 4) if d > 0 else 1.0 for sid, d in wanted.items()}


def fairness(demand: Mapping[tuple[str, str], Iterable[Any]], tick: int,
             window_ticks: int = HORIZON_TICKS) -> dict[str, Any]:
    """The distribution behind the headline service level."""
    per_station = station_service_levels(demand, since_tick=max(0, tick - window_ticks))
    if not per_station:
        return {"window_ticks": window_ticks, "per_station": {}, "jain": None, "min_station": None,
                "worst_station": None, "spread": None}
    values = list(per_station.values())
    jain = jain_index(values)
    worst = min(per_station, key=lambda s: per_station[s])
    if jain is not None:
        JAIN.set(jain)
    MIN_STATION_SL.set(min(values))
    return {
        "window_ticks": window_ticks,
        "per_station": per_station,
        "jain": jain,
        "min_station": min(values),
        "worst_station": worst,
        "spread": round(max(values) - min(values), 4),
    }


def diagnose(cover: Mapping[str, float | None], min_station: float | None,
             pending_approvals: int = 0, mode: str = "advisory") -> dict[str, str]:
    """Separate "there is not enough fuel" from "the fuel is not moving".

    These look identical on a stockout chart and need opposite responses, and we only learned to tell them apart
    by watching our own stack: every station bone dry while the depots still held 67,000 L of diesel. Nothing was
    scarce. Nobody had approved anything, because the platform was left in advisory mode overnight.
    """
    worst_cover = min((v for v in cover.values() if v is not None), default=None)
    if worst_cover is not None and worst_cover < ENTER_BELOW:
        return {"constraint": "supply",
                "detail": "there is not enough deliverable fuel to cover the forecast, however well it is routed"}
    if min_station is not None and min_station < 0.95:
        if mode != "autopilot" and pending_approvals > 0:
            return {"constraint": "approval",
                    "detail": (f"fuel is available but {pending_approvals} recommendation(s) are waiting for a "
                               f"human; in advisory mode nothing ships until someone approves")}
        return {"constraint": "distribution",
                "detail": ("fuel exists in the network but is not reaching stations: look at dispatch caps, "
                           "route status and transit times rather than at supply")}
    return {"constraint": "none", "detail": "supply covers the forecast and every station is being served"}


# --------------------------------------------------------------------------- the published document
def report(state: PlanningState, tracker: ScarcityTracker, demand: Mapping[tuple[str, str], Iterable[Any]],
           pending_approvals: int = 0, mode: str = "advisory") -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """Everything the console needs about scarcity and fairness, plus the transitions worth an incident."""
    ratios = cover_ratios(state)
    changes = tracker.update(ratios["network"])
    fair = fairness(demand, state.tick)
    ends = supply_ends_tick(state)
    doc = {
        "tick": state.tick,
        "horizon_ticks": HORIZON_TICKS,
        "active": dict(tracker.active),
        "cover_ratio": ratios["network"],
        "cover_ratio_by_region": ratios["regions"],
        "supply_ends_tick": ends,
        "living_off_stock": ends is None,
        "policy": ("equal cover: when fuel runs short the planner maximises the worst-served station's cover "
                   "rather than the network total"),
        "fairness": fair,
        "diagnosis": diagnose(ratios["network"], fair.get("min_station"), pending_approvals, mode),
        "thresholds": {"enter_below": ENTER_BELOW, "exit_above": EXIT_ABOVE,
                       "enter_cycles": ENTER_CYCLES, "exit_cycles": EXIT_CYCLES},
    }
    return doc, changes
