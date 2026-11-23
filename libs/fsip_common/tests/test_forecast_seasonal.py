"""The seasonal model must RECOVER the demand structure from data (nothing about the simulator is hard-coded)."""
from __future__ import annotations

import random

import pytest
from fsip_common.forecast_seasonal import HistoryPoint, MultiplierWindow, fit_profile, forecast, hour_of


def industrial(tick: int) -> float:
    """Shape like the simulator's industrial profile: 1.55 06:00-17:59, 0.45 otherwise (base 100 L/tick)."""
    h = hour_of(tick, 15)
    return 100.0 * (1.55 if 6 <= h < 18 else 0.45)


def synth(ticks: range, mult=lambda t: 1.0, noise=0.08, seed=1) -> list[HistoryPoint]:
    rng = random.Random(seed)
    return [HistoryPoint(t, industrial(t) * mult(t) * (1 + rng.uniform(-noise, noise)), mult(t)) for t in ticks]


def test_profile_recovers_hour_factors_within_noise():
    prof = fit_profile(synth(range(0, 192)), 15)
    assert prof[10] == pytest.approx(155, rel=0.05)
    assert prof[2] == pytest.approx(45, rel=0.05)


def test_forecast_tracks_day_night_pattern():
    p10, p50, p90 = forecast(synth(range(0, 192)), 192, 15, 96)
    assert p50[40] == pytest.approx(155, rel=0.06)   # tick 232 → 10:00
    assert p50[0] == pytest.approx(45, rel=0.06)     # tick 192 → 00:00
    assert all(a <= b <= c for a, b, c in zip(p10, p50, p90, strict=True))


def test_history_normalised_by_multiplier_so_a_past_spike_does_not_inflate_the_profile():
    hist = synth(range(0, 192), mult=lambda t: 1.8 if 100 <= t < 130 else 1.0)
    _, p50, _ = forecast(hist, 192, 15, 96, multiplier_now=1.0)
    assert p50[40] == pytest.approx(155, rel=0.06)


def test_active_multiplier_applies_immediately_and_scheduled_window_later():
    hist = synth(range(0, 192))
    _, now, _ = forecast(hist, 192, 15, 8, multiplier_now=1.8)
    assert now[0] == pytest.approx(45 * 1.8, rel=0.08)          # zero lag: the spike is in the very next tick
    _, sched, _ = forecast(hist, 192, 15, 96, windows=[MultiplierWindow(200, 210, 2.0)])
    assert sched[8] == pytest.approx(2 * sched[7], rel=0.05)     # tick 200 starts the scheduled spike
    assert sched[18] == pytest.approx(sched[17] * (sched[18] / sched[17]), rel=0.01)


def test_band_covers_about_80_percent_of_uniform_noise():
    hist = synth(range(0, 288), seed=3)
    p10, p50, p90 = forecast(hist[:192], 192, 15, 96)
    actual = [p.demand for p in hist[192:288]]
    inside = sum(lo <= a <= hi for lo, a, hi in zip(p10, actual, p90, strict=True)) / len(actual)
    assert 0.65 <= inside <= 0.95


def test_cold_start_and_garbage_rows():
    assert forecast([], 0, 15, 4) == ([0.0] * 4, [0.0] * 4, [0.0] * 4)
    p10, p50, p90 = forecast([HistoryPoint(0, float("nan")), HistoryPoint(1, 50.0)], 2, 15, 3)
    assert p50[0] == pytest.approx(50.0)
