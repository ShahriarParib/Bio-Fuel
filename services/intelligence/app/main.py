"""Intelligence service: demand forecasting (21 §4).

The champion model is the structural seasonal model in `fsip_common.forecast_seasonal` (the engine runs the same code
as its fallback, so "prediction service down" degrades capacity, not correctness). `INTEL_FORCE_GARBAGE=true` makes the
service return NaN/negative values: a drill that proves the engine validates model output instead of trusting it.
"""
from __future__ import annotations

import math
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fsip_common.forecast_seasonal import (
    MODEL_NAME,
    MODEL_VERSION,
    HistoryPoint,
    MultiplierWindow,
    forecast,
)
from fsip_common.logging import configure_logging, get_logger
from fsip_common.settings import BaseServiceSettings
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app
from pydantic import BaseModel, Field

log = get_logger()


class IntelSettings(BaseServiceSettings):
    intel_force_garbage: bool = False


settings = IntelSettings()
MODEL_INFO = Gauge("fsip_model_info", "Active forecast model", ["name", "version"])
FORECAST_REQUESTS = Counter("fsip_forecast_requests_total", "Forecast requests", ["result"])
FORECAST_SECONDS = Histogram("fsip_forecast_compute_seconds", "Forecast compute time",
                             buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5))


class Point(BaseModel):
    tick: int = Field(ge=0)
    demand: float = Field(ge=0, allow_inf_nan=False)
    multiplier: float = Field(gt=0, allow_inf_nan=False)


class Window(BaseModel):
    from_tick: int
    to_tick: int
    multiplier: float = Field(gt=0, allow_inf_nan=False)


class Series(BaseModel):
    station_id: str
    fuel_type: str
    history: list[Point] = Field(max_length=2000)
    multiplier_now: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    multiplier_schedule: list[Window] = Field(default_factory=list, max_length=50)


class ForecastRequest(BaseModel):
    tick: int = Field(ge=0)
    tick_minutes: int = Field(default=15, gt=0, le=1440)
    horizon: int = Field(default=96, ge=1, le=192)
    series: list[Series] = Field(max_length=100)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging("intelligence", settings.log_level)
    app.state.model = {"name": MODEL_NAME, "version": MODEL_VERSION}
    MODEL_INFO.labels(MODEL_NAME, MODEL_VERSION).set(1)
    log.info("intelligence.start", model=app.state.model, force_garbage=settings.intel_force_garbage)
    yield


app = FastAPI(title="fsip-intelligence", version=MODEL_VERSION, lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


@app.get("/health/live")
async def live():
    return {"status": "ok", "service": "intelligence"}


@app.get("/health/ready")
async def ready():
    return {"status": "ready", "model": app.state.model}


@app.get("/models")
async def models():
    meta = {**app.state.model, "description": "hour-of-day profile × live demand multiplier × EWMA level; "
                                              "p10/p90 from recent relative residuals"}
    return {"active": meta, "available": [meta]}


@app.post("/forecast")
async def post_forecast(req: ForecastRequest):
    t0 = time.perf_counter()
    out = []
    for s in req.series:
        hist = [HistoryPoint(p.tick, p.demand, p.multiplier) for p in s.history]
        wins = [MultiplierWindow(w.from_tick, w.to_tick, w.multiplier) for w in s.multiplier_schedule]
        p10, p50, p90 = forecast(hist, req.tick, req.tick_minutes, req.horizon, s.multiplier_now, wins)
        if settings.intel_force_garbage:
            p50 = [-v - 1.0 for v in p50]  # valid JSON, wrong values: the engine must reject it itself
        points = [{"tick": req.tick + k, "p10": p10[k], "p50": p50[k], "p90": p90[k]} for k in range(req.horizon)]
        out.append({"station_id": s.station_id, "fuel_type": s.fuel_type, "points": points})
    FORECAST_SECONDS.observe(time.perf_counter() - t0)
    if not settings.intel_force_garbage:
        bad = [p for s in out for p in s["points"]
               if not all(math.isfinite(p[k]) and p[k] >= 0 for k in ("p10", "p50", "p90"))]
        if bad:
            FORECAST_REQUESTS.labels(result="bad_output").inc()
            raise HTTPException(status_code=500, detail={"code": "BAD_MODEL_OUTPUT", "message": "non-finite forecast"})
    FORECAST_REQUESTS.labels(result="ok").inc()
    return {"model": MODEL_NAME, "version": MODEL_VERSION, "series": out}
