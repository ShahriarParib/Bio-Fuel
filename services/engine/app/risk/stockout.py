"""Stockout risk: Monte Carlo over forecast uncertainty.

For each (station, fuel): 300 demand paths from the p10/p50/p90 forecast (asymmetric normal per tick + a shared level
shock, so errors are correlated in time like real demand), our in-flight arrivals, and outage windows (demand at an
OUTAGE station is lost, not served). Output: probability of running dry within 12 h and within the resupply lead time,
the p50 time to stockout, and a risk level.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass

import numpy as np

from ..decision.planning import FUELS, Key, PlanningState

N_PATHS = 300
RISK_HORIZON = 48  # ticks (12 h at 15 min)


@dataclass
class Risk:
    station_id: str
    fuel_type: str
    level: str                     # LOW | MEDIUM | HIGH | CRITICAL
    prob_12h: float
    prob_lead: float
    hours_to_stockout: float | None
    inventory: float
    inflight: float
    capacity: float
    expected_demand_12h: float
    lead_ticks: int | None

    def as_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def _seed(run_id: int, tick: int, key: Key) -> int:
    return zlib.crc32(f"{run_id}:{tick}:{key[0]}:{key[1]}".encode())


def simulate(inv0: float, capacity: float, p10: np.ndarray, p50: np.ndarray, p90: np.ndarray,
             arrivals: np.ndarray, outage: np.ndarray, seed: int, n: int = N_PATHS) -> tuple[np.ndarray, int | None]:
    """Return (first stockout offset per path, H+1 if none) and the p50-path stockout offset."""
    h = len(p50)
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((n, h))
    sig_hi = np.maximum(p90 - p50, 1e-6) / 1.2816
    sig_lo = np.maximum(p50 - p10, 1e-6) / 1.2816
    d = p50 + np.where(z > 0, z * sig_hi, z * sig_lo)
    d *= rng.normal(1.0, 0.05, size=(n, 1))
    d = np.clip(d, 0, None)
    d = np.where(outage[None, :], 0.0, d)
    inv = np.full(n, float(inv0))
    first = np.full(n, h + 1)
    for k in range(h):
        inv = np.minimum(capacity, inv + arrivals[k]) - d[:, k]
        hit = (inv <= 0) & (first > h)
        first[hit] = k
        inv = np.maximum(inv, 0)
    t50, ip = None, float(inv0)
    for k in range(h):
        ip = min(capacity, ip + arrivals[k]) - (0.0 if outage[k] else p50[k])
        if ip <= 0 and t50 is None:
            t50 = k
        ip = max(ip, 0.0)
    return first, t50


def outage_mask(state: PlanningState, station_id: str, horizon: int) -> np.ndarray:
    mask = np.zeros(horizon, dtype=bool)
    st = state.stations[station_id]
    if st.status == "OUTAGE":
        mask[0] = True
    for e in state.events:
        if e.type != "station_outage" or e.status == "RESOLVED":
            continue
        ids = e.parameters.get("station_ids") or []
        if ids and station_id not in ids:
            continue
        lo, hi = max(e.start_tick - state.tick, 0), min(e.end_tick - state.tick, horizon)
        if hi > lo:
            mask[lo:hi] = True
    return mask


def level_for(prob_12h: float, prob_lead: float, hours: float | None, lead_h: float) -> str:
    if prob_lead >= 0.5 or (hours is not None and hours <= lead_h):
        return "CRITICAL"
    if prob_12h >= 0.5:
        return "HIGH"
    if prob_12h >= 0.2:
        return "MEDIUM"
    return "LOW"


def assess(state: PlanningState, p90: dict[Key, list[float]], run_id: int,
           lead_fn, horizon: int = RISK_HORIZON) -> dict[Key, Risk]:
    out: dict[Key, Risk] = {}
    for sid, st in state.stations.items():
        lead = lead_fn(state, sid)
        mask = outage_mask(state, sid, horizon)
        for f in FUELS:
            key = (sid, f)
            p50 = np.array([state.demand(key, j) for j in range(horizon)])
            p10 = np.array([state.demand(key, j, low=True) for j in range(horizon)])
            hi = np.array((p90.get(key) or [])[:horizon] or list(p50 * 1.12))
            if len(hi) < horizon:
                hi = np.pad(hi, (0, horizon - len(hi)), mode="edge")
            hi = np.maximum(hi, p50)
            arrivals = np.zeros(horizon)
            inflight = 0.0
            for i in state.inflight_for(key):
                inflight += i.quantity
                k = i.arrival_tick - state.tick
                if 0 <= k < horizon:
                    arrivals[k] += i.quantity
            inv = float(st.inventory[f])
            first, t50 = simulate(inv, float(st.capacity[f]), p10, p50, hi, arrivals, mask,
                                  _seed(run_id, state.tick, key))
            lead_ticks = lead if lead is not None else horizon
            prob_12h = float((first < horizon).mean())
            prob_lead = float((first <= lead_ticks + 1).mean())
            hours = None if t50 is None else round(t50 / state.ticks_per_hour, 2)
            out[key] = Risk(sid, f, level_for(prob_12h, prob_lead, hours, (lead_ticks + 1) / state.ticks_per_hour),
                            prob_12h, prob_lead, hours, inv, inflight, float(st.capacity[f]),
                            float(p50.sum()), lead)
    return out


def _paths(inv0: float, capacity: float, p10, p50, p90, arrivals, outage, seed: int, from_k: int,
           n: int = N_PATHS) -> tuple[float, float]:
    """(P(stockout at or after offset from_k), mean unmet liters over the horizon). Same seed → common random numbers,
    so 'before' and 'after' differ only by the shipment."""
    h = len(p50)
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((n, h))
    sig_hi = np.maximum(p90 - p50, 1e-6) / 1.2816
    sig_lo = np.maximum(p50 - p10, 1e-6) / 1.2816
    d = np.clip((p50 + np.where(z > 0, z * sig_hi, z * sig_lo)) * rng.normal(1.0, 0.05, size=(n, 1)), 0, None)
    d = np.where(outage[None, :], 0.0, d)
    inv = np.full(n, float(inv0))
    out_after = np.zeros(n, dtype=bool)
    unmet = np.zeros(n)
    for k in range(h):
        inv = np.minimum(capacity, inv + arrivals[k]) - d[:, k]
        short = inv < 0
        unmet += np.where(short, -inv, 0.0)
        if k >= from_k:
            out_after |= inv <= 0
        inv = np.maximum(inv, 0)
    return float(out_after.mean()), float(unmet.mean())


def shipment_impact(state: PlanningState, key: Key, arrival_tick: int, qty: float, run_id: int,
                    p90: dict[Key, list[float]], horizon: int = RISK_HORIZON) -> dict[str, float]:
    """Brief §9 'Stockout risk reduced 72% → 19%': risk from the arrival tick to 12 h ahead, without vs with the
    shipment, plus expected unmet liters. (Risk before the truck can arrive cannot be changed by it, so it is
    excluded.)"""
    sid, f = key
    st = state.stations[sid]
    p50 = np.array([state.demand(key, j) for j in range(horizon)])
    p10 = np.array([state.demand(key, j, low=True) for j in range(horizon)])
    hi = np.array(((p90.get(key) or [])[:horizon]) or list(p50 * 1.12))
    if len(hi) < horizon:
        hi = np.pad(hi, (0, horizon - len(hi)), mode="edge")
    hi = np.maximum(hi, p50)
    base = np.zeros(horizon)
    for i in state.inflight_for(key):
        k = i.arrival_tick - state.tick
        if 0 <= k < horizon:
            base[k] += i.quantity
    k_arr = min(max(arrival_tick - state.tick, 0), horizon - 1)
    with_ship = base.copy()
    with_ship[k_arr] += qty
    mask = outage_mask(state, sid, horizon)
    seed = _seed(run_id, state.tick, key)
    inv0, cap = float(st.inventory[f]), float(st.capacity[f])
    p_before, u_before = _paths(inv0, cap, p10, p50, hi, base, mask, seed, k_arr)
    p_after, u_after = _paths(inv0, cap, p10, p50, hi, with_ship, mask, seed, k_arr)
    return {"stockout_prob_before": round(p_before, 3), "stockout_prob_after": round(p_after, 3),
            "unmet_before": round(u_before), "unmet_after": round(u_after)}
