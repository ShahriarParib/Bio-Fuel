"""Forecast client: the intelligence service is primary; the same seasonal model runs in-process as the fallback.

Brief §12: "ML model unavailable → fallback". Timeout 1.5 s + circuit breaker; any error, timeout or bad output
(NaN, negative, p10 > p50 > p90 violated) → local model for this cycle, `fsip_fallback_activations_total` += 1, and the
decision confidence drops so autopilot steps back to human review.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field

import httpx
from fsip_common.forecast_seasonal import MODEL_NAME, MODEL_VERSION, forecast
from fsip_common.logging import get_logger
from prometheus_client import Counter, Gauge, Histogram

from ..decision.planning import FUELS, Key
from ..sim.circuit import CircuitBreaker
from ..state.store import WorldState
from .series import future_windows, history_points

log = get_logger()

FALLBACK_ACTIVATIONS = Counter("fsip_fallback_activations_total", "Fallback activations", ["component", "reason"])
FORECAST_LATENCY = Histogram("fsip_forecast_request_seconds", "Forecast round trip", ["source"],
                             buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 1.5, 3))
FORECAST_SOURCE = Gauge("fsip_forecast_remote", "1 = intelligence service, 0 = in-engine fallback")
FORECAST_WAPE = Gauge("fsip_forecast_wape", "Rolling 1-step-ahead WAPE of p50 (all series)")
FORECAST_COVERAGE = Gauge("fsip_forecast_interval_coverage", "Rolling share of actuals inside [p10, p90]")
FORECAST_NOISE_FLOOR = Gauge("fsip_forecast_noise_floor_wape",
                             "WAPE a perfect model would still have, estimated from the residual band")


@dataclass
class ForecastBundle:
    tick: int
    p10: dict[Key, list[float]]
    p50: dict[Key, list[float]]
    p90: dict[Key, list[float]]
    source: str            # "intelligence" | "fallback"
    model: str             # e.g. seasonal@1.0.0
    reason: str | None = None


@dataclass
class _Accuracy:
    abs_err: deque = field(default_factory=lambda: deque(maxlen=1152))   # 96 ticks × 12 series
    actual: deque = field(default_factory=lambda: deque(maxlen=1152))
    inside: deque = field(default_factory=lambda: deque(maxlen=1152))
    half_band: deque = field(default_factory=lambda: deque(maxlen=1152))


class ForecastService:
    def __init__(self, url: str, timeout: float = 1.5, horizon: int = 96) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.horizon = horizon
        self.breaker = CircuitBreaker("intelligence", failure_threshold=3, reset_timeout=10.0)
        self.http = httpx.AsyncClient(timeout=timeout)
        self.last: ForecastBundle | None = None
        self.acc = _Accuracy()
        self.status = "UNKNOWN"  # Healthy | Down (as seen from real calls)

    async def close(self) -> None:
        await self.http.aclose()

    def _series(self, ws: WorldState) -> list[dict]:
        snap = ws.snapshot
        events = list(snap.events)
        out = []
        for st in snap.stations:
            windows = [{"from_tick": w.from_tick, "to_tick": w.to_tick, "multiplier": w.multiplier}
                       for w in future_windows(st, events)]
            for f in FUELS:
                pts = history_points(ws.demand.get((st.id, f), []), st, events)
                out.append({"station_id": st.id, "fuel_type": f,
                            "history": [{"tick": p.tick, "demand": p.demand, "multiplier": p.multiplier} for p in pts],
                            "multiplier_now": float(st.demand_multiplier), "multiplier_schedule": windows})
        return out

    def _local(self, ws: WorldState, series: list[dict], reason: str) -> ForecastBundle:
        from fsip_common.forecast_seasonal import HistoryPoint, MultiplierWindow

        inst = ws.snapshot.instance
        p10, p50, p90 = {}, {}, {}
        for s in series:
            hist = [HistoryPoint(h["tick"], h["demand"], h["multiplier"]) for h in s["history"]]
            wins = [MultiplierWindow(w["from_tick"], w["to_tick"], w["multiplier"]) for w in s["multiplier_schedule"]]
            a, b, c = forecast(hist, inst.tick, inst.tick_minutes, self.horizon, s["multiplier_now"], wins)
            key = (s["station_id"], s["fuel_type"])
            p10[key], p50[key], p90[key] = a, b, c
        return ForecastBundle(inst.tick, p10, p50, p90, "fallback", f"{MODEL_NAME}@builtin", reason)

    @staticmethod
    def _valid(points: list[dict], horizon: int) -> bool:
        if len(points) < horizon:
            return False
        for p in points:
            vals = (p.get("p10"), p.get("p50"), p.get("p90"))
            if any(v is None or not math.isfinite(v) or v < 0 for v in vals):
                return False
            if not (vals[0] <= vals[1] + 1e-6 and vals[1] <= vals[2] + 1e-6):
                return False
        return True

    async def refresh(self, ws: WorldState) -> ForecastBundle:
        inst = ws.snapshot.instance
        if self.last is not None and self.last.tick == inst.tick:
            return self.last
        self._score(ws)
        series = self._series(ws)
        bundle: ForecastBundle | None = None
        reason = None
        if self.breaker.allow():
            t0 = time.perf_counter()
            try:
                resp = await self.http.post(f"{self.url}/forecast", json={
                    "tick": inst.tick, "tick_minutes": inst.tick_minutes, "horizon": self.horizon, "series": series})
                FORECAST_LATENCY.labels(source="intelligence").observe(time.perf_counter() - t0)
                resp.raise_for_status()
                body = resp.json()
                p10, p50, p90 = {}, {}, {}
                for s in body.get("series", []):
                    if not self._valid(s.get("points", []), self.horizon):
                        raise ValueError(f"bad model output for {s.get('station_id')}/{s.get('fuel_type')}")
                    key = (s["station_id"], s["fuel_type"])
                    p10[key] = [p["p10"] for p in s["points"]][: self.horizon]
                    p50[key] = [p["p50"] for p in s["points"]][: self.horizon]
                    p90[key] = [p["p90"] for p in s["points"]][: self.horizon]
                if len(p50) != len(series):
                    raise ValueError("model returned fewer series than requested")
                self.breaker.record_success()
                bundle = ForecastBundle(inst.tick, p10, p50, p90, "intelligence",
                                        f"{body.get('model', MODEL_NAME)}@{body.get('version', MODEL_VERSION)}")
                self.status = "Healthy"
            except Exception as err:
                self.breaker.record_failure()
                reason = f"{type(err).__name__}: {str(err)[:120]}"
        else:
            reason = "breaker open"
        if bundle is None:
            self.status = "Down"
            FALLBACK_ACTIVATIONS.labels(component="forecast", reason=reason.split(":")[0][:40]).inc()
            if self.last is None or self.last.source != "fallback":
                log.warning("fallback.activated", component="forecast", reason=reason)
            bundle = self._local(ws, series, reason or "unknown")
        elif self.last is not None and self.last.source == "fallback":
            log.info("fallback.deactivated", component="forecast")
        FORECAST_SOURCE.set(1 if bundle.source == "intelligence" else 0)
        self.last = bundle
        return bundle

    def _score(self, ws: WorldState) -> None:
        """Compare the previous forecast's 1-step-ahead values with the demand that actually happened."""
        prev = self.last
        if prev is None:
            return
        for key, obs in ws.demand.items():
            for o in list(obs)[-24:]:
                k = o.tick - prev.tick
                if k != 0 or key not in prev.p50:
                    continue
                p10, p50, p90 = prev.p10[key][0], prev.p50[key][0], prev.p90[key][0]
                self.acc.abs_err.append(abs(o.demand_liters - p50))
                self.acc.actual.append(o.demand_liters)
                self.acc.inside.append(1.0 if p10 <= o.demand_liters <= p90 else 0.0)
                self.acc.half_band.append((p90 - p10) / 2.0)
        total = sum(self.acc.actual)
        if total > 0:
            FORECAST_WAPE.set(sum(self.acc.abs_err) / total)
            FORECAST_COVERAGE.set(sum(self.acc.inside) / len(self.acc.inside))
            # the 80% band of a uniform ±w noise is ±0.8w → w ≈ half_band / 0.8; perfect-model MAE = w/2
            FORECAST_NOISE_FLOOR.set((sum(self.acc.half_band) / 0.8 / 2) / total)

    def accuracy(self) -> dict:
        total = sum(self.acc.actual)
        if not total:
            return {"wape": None, "coverage_80": None, "noise_floor_wape": None, "n": 0}
        return {"wape": round(sum(self.acc.abs_err) / total, 4),
                "coverage_80": round(sum(self.acc.inside) / len(self.acc.inside), 3),
                "noise_floor_wape": round((sum(self.acc.half_band) / 0.8 / 2) / total, 4),
                "n": len(self.acc.actual)}
