"""N-1 contingency analysis: which single asset, if we lost it, would actually hurt.

The twin itself lives in `fsip_common.twin` and knows nothing about our services. This module is the bridge: it
turns the engine's planning view into a twin world, runs the scan, and shapes the result for the console.

Why the engine runs it rather than the intelligence service: the engine already holds the forecast, the in-flight
shipments and the live network, and the scan is pure CPU with no I/O. Running it anywhere else would mean shipping
the whole world over HTTP every few minutes to compute something we can do in half a second here.
"""
from __future__ import annotations

import time
from typing import Any

from fsip_common.twin import (
    TwinDepot,
    TwinRoute,
    TwinStation,
    TwinSupply,
    TwinWorld,
    n1_scan,
)
from prometheus_client import Gauge, Histogram

from ..decision.planning import FUELS, PlanningState

N1_CRITICAL = Gauge("fsip_n1_critical_assets", "Assets whose loss would empty a station before fuel could reroute")
N1_WORST = Gauge("fsip_n1_worst_extra_unmet_liters", "Avoidable litres the worst single-asset failure would cost")
N1_SECONDS = Histogram("fsip_n1_scan_seconds", "Time to scan every asset", buckets=(0.1, 0.25, 0.5, 1, 2, 5))

SCAN_EVERY_TICKS = 16
HORIZON_TICKS = 96
DURATIONS = (24, 48)


def world_from(state: PlanningState) -> TwinWorld:
    """Project the engine's planning view into the twin's vocabulary.

    Only routes the simulator currently calls AVAILABLE are usable; a route that is already disrupted is not a
    contingency we are asking about, it is today's reality, and the baseline must carry it.
    """
    stations = tuple(
        TwinStation(id=s.id,
                    capacity={f: float(s.capacity.get(f, 0.0)) for f in FUELS},
                    inventory={f: float(s.inventory.get(f, 0.0)) for f in FUELS})
        for s in state.stations.values()
    )
    depots = tuple(
        TwinDepot(id=d.id,
                  inventory={f: float(d.inventory.get(f, 0.0)) for f in FUELS},
                  # a CONSTRAINED depot signals reduced capacity; we apply the same self-imposed cut as the planner
                  dispatch_per_tick=float(d.dispatch_capacity_per_tick) * (0.5 if d.status == "CONSTRAINED" else 1.0))
        for d in state.depots.values()
    )
    routes = tuple(
        TwinRoute(id=r.id, depot_id=r.source_depot_id, station_id=r.destination_station_id,
                  transit_ticks=int(r.transit_ticks), max_shipment=float(r.max_shipment),
                  available=r.status == "AVAILABLE")
        for r in state.routes.values()
    )
    supply = tuple(
        TwinSupply(tick=int(a.planned_tick), depot_id=a.depot_id, fuel=a.fuel_type, quantity=float(a.quantity))
        for a in state.supply if a.planned_tick >= state.tick
    )
    inflight = tuple((int(i.arrival_tick), i.station_id, i.fuel_type, float(i.quantity)) for i in state.inflight)
    return TwinWorld(
        tick=state.tick, tick_minutes=state.tick_minutes, stations=stations, depots=depots, routes=routes,
        demand={k: tuple(v) for k, v in state.demand_p50.items()}, supply=supply, inflight=inflight,
    )


def _labels(state: PlanningState) -> dict[str, str]:
    out = {s.id: s.name for s in state.stations.values()}
    out.update({d.id: d.name for d in state.depots.values()})
    for r in state.routes.values():
        src = state.depots.get(r.source_depot_id)
        dst = state.stations.get(r.destination_station_id)
        out[r.id] = f"{(src.name if src else r.source_depot_id)} → {(dst.name if dst else r.destination_station_id)}"
    return out


def _advice(finding: dict[str, Any], state: PlanningState) -> str:
    """The sentence an operator can act on today, not a restatement of the number."""
    label = finding["label"]
    # for a route, the station it feeds is the one the advice is about; worst_station is a network-wide answer
    station_id = finding.get("serves") or finding.get("worst_station")
    station = state.stations.get(station_id or "")
    who = station.name if station is not None else "the station it feeds"
    if finding["kind"] == "route":
        if finding.get("sole_supply_path"):
            return (f"{who} has no second route at all. Pre-stock it before {label} is ever cut: nothing can "
                    f"reach it while that route is down.")
        if finding.get("only_working_route"):
            return (f"{label} is {who}'s only *working* route right now - its alternatives are disrupted. "
                    f"This is today's exposure, not a permanent one.")
        return (f"If {label} is cut, fuel can still reach {who} another way in about {finding['ttr_hours']} h; "
                f"keep roughly that much cover in the tank.")
    if finding["kind"] == "depot":
        return f"Losing {label} removes every route out of it. Keep the stations it feeds above {label}'s transit time."
    return f"An outage at {label} costs its own sales only; the rest of the network is unaffected."


def scan(state: PlanningState) -> dict[str, Any]:
    """Run the full N-1 scan and shape it for the console."""
    started = time.monotonic()
    world = world_from(state)
    labels = _labels(state)
    result = n1_scan(world, horizon=HORIZON_TICKS, durations=DURATIONS, names=labels)
    elapsed = time.monotonic() - started
    N1_SECONDS.observe(elapsed)

    critical = [f for f in result["findings"] if f["critical"]]
    N1_CRITICAL.set(len({f["asset"] for f in critical}))
    N1_WORST.set(max((f["extra_unmet_vs_normal"] for f in result["findings"]), default=0.0))

    for f in result["findings"]:
        f["station_label"] = labels.get(f.get("worst_station") or "", f.get("worst_station"))
    for f in result["top_critical"] + result["worst_assets"]:
        f["advice"] = _advice(f, state)
        f["station_label"] = labels.get(f.get("worst_station") or "", f.get("worst_station"))

    result["computed_in_ms"] = round(elapsed * 1000)
    result["stale"] = bool(state.stale or state.degraded)
    result["forecast_model"] = state.forecast_model
    return result


def should_run(tick: int, last_tick: int | None) -> bool:
    return last_tick is None or tick - last_tick >= SCAN_EVERY_TICKS
