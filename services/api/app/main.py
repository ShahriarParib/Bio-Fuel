"""Operator API (phase-00 skeleton). Routers are added per 21_API_CONTRACTS.md (state, stream, risk, alerts,
recommendations, decisions, system, whatif, assistant, control, auth, ledger, replay...)."""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fsip_common.logging import configure_logging, get_logger
from fsip_common.settings import BaseServiceSettings
from prometheus_client import Counter, Histogram, make_asgi_app

log = get_logger()
settings = BaseServiceSettings()
REQS = Counter("http_requests_total", "HTTP requests", ["service", "route", "method", "status"])
LAT = Histogram("http_request_duration_seconds", "HTTP latency", ["service", "route"],
                buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5))


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging("api", settings.log_level)
    app.state.redis = aioredis.from_url(settings.redis_url, socket_timeout=1, socket_connect_timeout=1)
    app.state.http = httpx.AsyncClient(timeout=1.5)
    yield
    await app.state.http.aclose()
    await app.state.redis.aclose()


app = FastAPI(title="fsip-api", version="0.1.0", lifespan=lifespan, docs_url="/api/docs",
              openapi_url="/api/openapi.json")
app.mount("/metrics", make_asgi_app())


@app.middleware("http")
async def metrics_mw(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    path = getattr(route, "path", "unmatched")          # route template -> low cardinality
    if not path.startswith("/metrics"):
        REQS.labels("api", path, request.method, str(response.status_code)).inc()
        LAT.labels("api", path).observe(time.perf_counter() - t0)
    return response


@app.get("/health/live")
async def live():
    return {"status": "ok", "service": "api"}


@app.get("/health/ready")
async def ready():
    try:
        await app.state.redis.ping()
        return {"status": "ready", "redis": "UP"}
    except Exception:  # noqa: BLE001
        # still serve (DB fallback in phase 02), but report degraded
        return {"status": "degraded", "redis": "DOWN"}


@app.get("/api/ping")
async def ping():
    return {"pong": True, "version": os.getenv("APP_VERSION", "dev")}


async def _probe(name: str, url: str) -> tuple[str, dict]:
    t0 = time.perf_counter()
    try:
        r = await app.state.http.get(url)
        ok = r.status_code == 200
        return name, {"status": "Healthy" if ok else "Degraded", "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
    except Exception as e:  # noqa: BLE001
        return name, {"status": "Down", "error": type(e).__name__}


@app.get("/api/system/status")
async def system_status():
    """Phase-00 version: probes components directly. Phase 02/10 extends it to the full shape in 21_API_CONTRACTS.md."""
    probes = await asyncio.gather(
        _probe("decision_engine", f"{settings.engine_internal_url}/health/ready"),
        _probe("prediction_service", f"{settings.intelligence_url}/health/ready"),
        _probe("fuel_simulator", f"{settings.sim_base_url}/v1/health"),
    )
    comps = dict(probes)
    try:
        await app.state.redis.ping()
        comps["redis"] = {"status": "Healthy"}
    except Exception:  # noqa: BLE001
        comps["redis"] = {"status": "Down"}
    comps["backend_api"] = {"status": "Healthy"}
    worst = "Healthy"
    if any(c["status"] == "Down" for c in comps.values()):
        worst = "Down" if comps["fuel_simulator"]["status"] == "Down" else "Degraded"
    elif any(c["status"] == "Degraded" for c in comps.values()):
        worst = "Degraded"
    return {"overall": worst, "components": comps,
            "versions": {"api": os.getenv("APP_VERSION", "dev"), "git_sha": os.getenv("GIT_SHA", "unknown")}}
