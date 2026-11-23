"""Structural seasonal forecaster (the champion model AND the engine's built-in fallback: same code in both places).

Demand in this network is (hour-of-day profile) × (live demand multiplier) × (bounded noise). Instead of asking a
generic regressor to rediscover that from lags, we model it directly and learn every number from observed data:

    base[h]   = median over history of demand / multiplier_at_that_tick, for hour-of-day h
    level     = EWMA(alpha=0.3) of actual / (base * multiplier) over the last 8 observations  (absorbs drift)
    p50(t+k)  = base[hour(t+k)] * multiplier(t+k) * level
    p10/p90   = p50 * (1 + q10/q90 of recent relative residuals)

`multiplier(t+k)` comes from the simulator's own state (station.demand_multiplier) plus scheduled demand_spike events,
so a spike moves the forecast on the tick it starts instead of hours later (a lag-feature model reacts only after the
damage). Nothing about the simulator's profile constants is hard-coded: tests check that they are *recovered*.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

MODEL_NAME = "seasonal"
MODEL_VERSION = "1.0.0"
DEFAULT_REL_BAND = 0.12  # cold-start p10/p90 half-width until 24 residuals exist


@dataclass(frozen=True)
class HistoryPoint:
    tick: int
    demand: float
    multiplier: float = 1.0


@dataclass(frozen=True)
class MultiplierWindow:
    from_tick: int
    to_tick: int  # exclusive
    multiplier: float  # factor applied on top of the current multiplier inside the window


def hour_of(tick: int, tick_minutes: int) -> int:
    return int((tick * tick_minutes) // 60) % 24


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _quantile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    if not s:
        return 0.0
    pos = q * (len(s) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def fit_profile(history: Sequence[HistoryPoint], tick_minutes: int) -> dict[int, float]:
    """Median normalised demand per hour of day; hours never seen fall back to the overall median."""
    by_hour: dict[int, list[float]] = {}
    for p in history:
        if p.multiplier > 0:
            by_hour.setdefault(hour_of(p.tick, tick_minutes), []).append(p.demand / p.multiplier)
    if not by_hour:
        return {}
    seen = {h: _median(vs) for h, vs in by_hour.items()}
    # Cold start (< 1 day of history): an hour never seen takes the value of the nearest earlier hour that was seen
    # (demand moves in day/night blocks, so "continue the current level" beats "use the daily median").
    out: dict[int, float] = {}
    for h in range(24):
        for back in range(24):
            prev = (h - back) % 24
            if prev in seen:
                out[h] = seen[prev]
                break
    return out


def forecast(history: Sequence[HistoryPoint], tick_now: int, tick_minutes: int, horizon: int,
             multiplier_now: float = 1.0, windows: Sequence[MultiplierWindow] = (),
             ) -> tuple[list[float], list[float], list[float]]:
    """Return (p10, p50, p90) for ticks tick_now .. tick_now+horizon-1. History must be oldest → newest."""
    hist = [p for p in history if p.demand >= 0 and math.isfinite(p.demand)]
    profile = fit_profile(hist, tick_minutes)
    if not profile:
        flat = 0.0
        return [flat] * horizon, [flat] * horizon, [flat] * horizon

    # level: how the last observations compare with the profile (EWMA, clipped)
    level = 1.0
    for p in hist[-8:]:
        expected = profile[hour_of(p.tick, tick_minutes)] * p.multiplier
        if expected > 0:
            level = 0.7 * level + 0.3 * (p.demand / expected)
    level = min(1.3, max(0.7, level))

    # relative residual band from recent history
    rel = []
    for p in hist[-192:]:
        expected = profile[hour_of(p.tick, tick_minutes)] * p.multiplier
        if expected > 0:
            rel.append(p.demand / expected - 1.0)
    if len(rel) >= 24:
        # residuals are in-sample (the profile was fit on the same rows), so their 10/90% band under-covers out of
        # sample (measured: 64% instead of 80%); the 5/95% in-sample band is the calibrated 80% interval
        lo, hi = _quantile(rel, 0.05), _quantile(rel, 0.95)
    else:
        lo, hi = -DEFAULT_REL_BAND, DEFAULT_REL_BAND
    lo, hi = min(lo, -0.02), max(hi, 0.02)

    p10, p50, p90 = [], [], []
    for k in range(horizon):
        t = tick_now + k
        m = multiplier_now
        for w in windows:
            if w.from_tick <= t < w.to_tick:
                m *= w.multiplier
        mid = max(0.0, profile[hour_of(t, tick_minutes)] * m * level)
        p50.append(mid)
        p10.append(max(0.0, mid * (1 + lo)))
        p90.append(mid * (1 + hi))
    return p10, p50, p90


def noise_floor_mae(rel_half_width: float) -> float:
    """MAE (relative to the mean) of a perfect forecast under uniform ±w multiplicative noise = w/2."""
    return rel_half_width / 2.0
