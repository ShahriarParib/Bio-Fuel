"""Build forecast inputs from the world: demand history with the multiplier that was active at each tick, plus the
future multiplier schedule.

The simulator only exposes the *current* `station.demand_multiplier`, but every demand_spike event keeps its
start/end ticks and factor (also after it resolves). So the multiplier of any past tick is reconstructed exactly from
the event list instead of being sampled (sampling misses ticks when the simulator runs fast and cycles coalesce).
"""
from __future__ import annotations

from fsip_common.forecast_seasonal import HistoryPoint, MultiplierWindow
from fsip_common.models import SimEvent, Station

BIG = 10**9


def _applies(e: SimEvent, station: Station) -> bool:
    p = e.parameters or {}
    sids, rids = p.get("station_ids") or [], p.get("region_ids") or []
    return (not sids and not rids) or station.id in sids or station.region_id in rids


def spike_events(events: list[SimEvent], station: Station) -> list[tuple[int, int, float, str]]:
    out = []
    for e in events:
        if e.type == "demand_spike" and _applies(e, station):
            m = float((e.parameters or {}).get("multiplier", 1.5))
            if m > 0:
                out.append((e.start_tick, e.end_tick, m, e.status))
    return out


def base_multiplier(station: Station, events: list[SimEvent]) -> float:
    """Station multiplier without the currently ACTIVE spikes (1.0 in every shipped scenario)."""
    active = 1.0
    for _s, _e, m, status in spike_events(events, station):
        if status == "ACTIVE":
            active *= m
    return float(station.demand_multiplier) / active if active > 0 else float(station.demand_multiplier)


def multiplier_at(station: Station, events: list[SimEvent], tick: int, base: float) -> float:
    m = base
    for start, end, factor, status in spike_events(events, station):
        if status != "SCHEDULED" and start <= tick < end:
            m *= factor
    return m


def future_windows(station: Station, events: list[SimEvent]) -> list[MultiplierWindow]:
    """Changes to apply on top of the current multiplier: scheduled spikes start, active spikes end."""
    out = []
    for start, end, factor, status in spike_events(events, station):
        if status == "SCHEDULED":
            out.append(MultiplierWindow(start, end, factor))
        elif status == "ACTIVE":
            out.append(MultiplierWindow(end, BIG, 1.0 / factor))
    return out


def history_points(observations, station: Station, events: list[SimEvent], limit: int = 192) -> list[HistoryPoint]:
    base = base_multiplier(station, events)
    obs = list(observations)[-limit:]
    return [HistoryPoint(o.tick, float(o.demand_liters), multiplier_at(station, events, o.tick, base)) for o in obs]
