"""Intelligence service (phase-00 skeleton). Phase 05 adds /forecast + model registry; phase 07 /anomaly/score;
phase 18 /lab/* (N-1, policy comparison). Contracts: 21_API_CONTRACTS.md §3."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fsip_common.logging import configure_logging, get_logger
from fsip_common.settings import BaseServiceSettings
from prometheus_client import Gauge, make_asgi_app

log = get_logger()
MODEL_INFO = Gauge("fsip_model_info", "Active forecast model", ["name", "version"])
settings = BaseServiceSettings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging("intelligence", settings.log_level)
    # PHASE 05: load artifacts/active.json -> model; fall back to the seasonal model if missing
    app.state.model = {"name": "seasonal", "version": "builtin"}
    MODEL_INFO.labels("seasonal", "builtin").set(1)
    log.info("intelligence.start", model=app.state.model)
    yield


app = FastAPI(title="fsip-intelligence", version="0.1.0", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


@app.get("/health/live")
async def live():
    return {"status": "ok", "service": "intelligence"}


@app.get("/health/ready")
async def ready():
    return {"status": "ready", "model": app.state.model}


@app.get("/models")
async def models():
    return {"active": app.state.model, "available": [app.state.model]}
