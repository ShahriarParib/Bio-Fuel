"""Operator API service: provides REST endpoints and SSE stream to frontend."""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fsip_common.logging import configure_logging, get_logger
from prometheus_client import Counter, Histogram, make_asgi_app
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import ApiSettings, get_settings
from .routers import nodes, state

log = get_logger()
settings = get_settings()

REQS = Counter(
    "http_requests_total",
    "HTTP requests",
    ["service", "route", "method", "status"],
)
LAT = Histogram(
    "http_request_duration_seconds",
    "HTTP latency",
    ["service", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)

# Rolling window for in-process p95 latency and error rate calculation
_recent_latencies: deque[float] = deque(maxlen=500)
_recent_status_codes: deque[int] = deque(maxlen=500)

# In-memory rate limiting fallback: ip -> deque of timestamps
_rate_limit_buckets: defaultdict[str, deque[float]] = defaultdict(lambda: deque(maxlen=1000))


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging("api", settings.log_level)

    # 1. Connect to Redis
    try:
        app.state.redis = aioredis.from_url(
            settings.redis_url,
            socket_timeout=1.5,
            socket_connect_timeout=1.5,
            decode_responses=True,
        )
        await app.state.redis.ping()
        log.info("api.redis_connected")
    except Exception as err:
        log.warning("api.redis_connect_failed", error=str(err))
        app.state.redis = None

    # 2. Database async engine & sessionmaker (soft initialization)
    try:
        engine = create_async_engine(
            settings.database_url,
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,
        )
        app.state.db_sessionmaker = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        log.info("api.db_configured")
    except Exception as db_err:
        log.warning("api.db_config_failed", error=str(db_err))
        app.state.db_sessionmaker = None

    app.state.http = httpx.AsyncClient(timeout=2.0)

    yield

    # Teardown
    await app.state.http.aclose()
    if getattr(app.state, "redis", None):
        await app.state.redis.aclose()


app = FastAPI(
    title="fsip-api",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.mount("/metrics", make_asgi_app())

# Include sub-routers
app.include_router(state.router)
app.include_router(nodes.router)


@app.middleware("http")
async def rate_limit_and_metrics_middleware(request: Request, call_next):
    """Enforce rate limits and track Prometheus and rolling window metrics."""
    client_ip = request.client.host if request.client else "127.0.0.1"
    now = time.monotonic()

    # Rate limiting (bypass metrics, health, docs)
    path = request.url.path
    if not (path.startswith("/health") or path.startswith("/metrics") or path.startswith("/api/docs")):
        is_limited = False
        redis_client = getattr(request.app.state, "redis", None)
        if redis_client:
            try:
                key = f"fsip:ratelimit:{client_ip}"
                current = await redis_client.incr(key)
                if current == 1:
                    await redis_client.expire(key, 60)
                if current > settings.api_rate_limit_per_min:
                    is_limited = True
            except Exception:
                # Redis failure -> fall back to memory
                pass

        if not is_limited:
            # Memory check fallback
            bucket = _rate_limit_buckets[client_ip]
            while bucket and now - bucket[0] > 60.0:
                bucket.popleft()
            if len(bucket) >= settings.api_rate_limit_per_min:
                is_limited = True
            else:
                bucket.append(now)

        if is_limited:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": {
                        "code": "RATE_LIMITED",
                        "message": f"Rate limit of {settings.api_rate_limit_per_min} req/min exceeded",
                    }
                },
            )

    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - t0

    route = request.scope.get("route")
    route_template = getattr(route, "path", path)
    if not route_template.startswith("/metrics"):
        REQS.labels("api", route_template, request.method, str(response.status_code)).inc()
        LAT.labels("api", route_template).observe(elapsed)

        _recent_latencies.append(elapsed * 1000.0)
        _recent_status_codes.append(response.status_code)

    return response


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    """Ensure all API errors conform to {"detail": {"code": ..., "message": ...}} standard."""
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": {
                "code": f"HTTP_{exc.status_code}",
                "message": str(exc.detail),
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def custom_validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "VALIDATION_ERROR",
                "message": "Invalid request schema",
                "errors": exc.errors(),
            }
        },
    )


@app.get("/health/live")
async def live():
    return {"status": "ok", "service": "api"}


@app.get("/health/ready")
async def ready():
    redis_status = "DOWN"
    redis_client = getattr(app.state, "redis", None)
    if redis_client:
        try:
            await redis_client.ping()
            redis_status = "UP"
        except Exception:
            pass

    return {
        "status": "ready" if redis_status == "UP" else "degraded",
        "redis": redis_status,
        "database": "CONFIGURED" if getattr(app.state, "db_sessionmaker", None) else "DOWN",
    }


@app.get("/api/ping")
async def ping():
    return {"pong": True, "version": os.getenv("APP_VERSION", "dev")}


async def _probe_component(name: str, url: str) -> tuple[str, dict[str, Any]]:
    t0 = time.perf_counter()
    try:
        r = await app.state.http.get(url)
        ok = r.status_code == 200
        latency = round((time.perf_counter() - t0) * 1000, 1)
        data = r.json() if ok else {}
        return name, {
            "status": "Healthy" if ok else "Degraded",
            "latency_ms": latency,
            "details": data,
        }
    except Exception as e:
        return name, {"status": "Down", "error": type(e).__name__}


@app.get("/api/system/status")
async def system_status():
    """Aggregate health status from all subsystems, redis, and recent metrics."""
    probes = await asyncio.gather(
        _probe_component("decision_engine", f"{settings.engine_internal_url}/health/ready"),
        _probe_component("prediction_service", f"{settings.intelligence_url}/health/ready"),
        _probe_component("fuel_simulator", f"{settings.sim_base_url}/v1/health"),
    )
    comps = dict(probes)

    redis_client = getattr(app.state, "redis", None)
    redis_healthy = False
    if redis_client:
        try:
            await redis_client.ping()
            redis_healthy = True
            comps["redis"] = {"status": "Healthy"}
        except Exception:
            comps["redis"] = {"status": "Down"}
    else:
        comps["redis"] = {"status": "Down"}

    comps["backend_api"] = {"status": "Healthy"}
    comps["database"] = {
        "status": "Healthy" if getattr(app.state, "db_sessionmaker", None) else "Degraded"
    }

    # Compute p95 latency and error rate from rolling window
    p95_ms = 0.0
    if _recent_latencies:
        sorted_lats = sorted(_recent_latencies)
        p95_idx = int(len(sorted_lats) * 0.95)
        p95_ms = round(sorted_lats[min(p95_idx, len(sorted_lats) - 1)], 1)

    error_rate = 0.0
    if _recent_status_codes:
        error_count = sum(1 for c in _recent_status_codes if c >= 500)
        error_rate = round(error_count / len(_recent_status_codes), 3)

    worst = "Healthy"
    if any(c.get("status") == "Down" for c in comps.values()):
        worst = "Down" if comps.get("fuel_simulator", {}).get("status") == "Down" else "Degraded"
    elif any(c.get("status") == "Degraded" for c in comps.values()):
        worst = "Degraded"

    engine_mode = "NORMAL"
    if "decision_engine" in comps and "details" in comps["decision_engine"]:
        engine_mode = comps["decision_engine"]["details"].get("engine_mode", "NORMAL")

    return {
        "overall": worst,
        "engine_mode": engine_mode,
        "components": comps,
        "versions": {
            "api": os.getenv("APP_VERSION", "dev"),
            "git_sha": os.getenv("GIT_SHA", "unknown"),
        },
        "p95_latency_ms": p95_ms,
        "error_rate": error_rate,
    }
