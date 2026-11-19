"""Engine service (phase-00 skeleton). Later phases add: sim/ (client, sse, validation), state/ (store, sync),
risk/, detect/, decision/, explain/, alerts/, db/ — see 03_ARCHITECTURE.md §3 and 13_ARCHITECTURE_V2.md.
The control loop is started in `lifespan` (phase 01)."""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fsip_common.logging import configure_logging, get_logger
from prometheus_client import Counter, Gauge, make_asgi_app

from .config import EngineSettings, get_settings

log = get_logger()
ENGINE_INFO = Gauge("fsip_engine_info", "Engine build info", ["version", "git_sha"])
READY_CHECKS = Counter("fsip_ready_checks_total", "Readiness checks", ["result"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging("engine", s.log_level)
    ENGINE_INFO.labels(s.app_version, s.git_sha).set(1)
    app.state.http = httpx.AsyncClient(base_url=s.sim_base_url, timeout=s.sim_timeout_sec)
    log.info("engine.start", sim=s.sim_base_url, mode=s.engine_mode, policy=s.engine_policy)
    # PHASE 01: start supervised SyncLoop + SSEListener tasks here (asyncio.create_task + restart on crash)
    yield
    await app.state.http.aclose()
    log.info("engine.stop")


app = FastAPI(title="fsip-engine", version="0.1.0", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


def require_internal(x_internal_token: str | None = Header(default=None),
                     s: EngineSettings = Depends(get_settings)) -> None:
    if x_internal_token != s.engine_internal_token:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "bad internal token"})


@app.get("/health/live")
async def live():
    return {"status": "ok", "service": "engine"}


@app.get("/health/ready")
async def ready():
    """Phase 00: ready if the simulator answers. Phase 01 changes this to: last successful sync < 30 s OR serving
    cached state in degraded mode (then 200 with degraded=true)."""
    try:
        r = await app.state.http.get("/v1/health")
        r.raise_for_status()
        READY_CHECKS.labels("ok").inc()
        return {"status": "ready", "simulator": r.json()}
    except Exception as e:  # noqa: BLE001 - readiness must never raise
        READY_CHECKS.labels("fail").inc()
        raise HTTPException(status_code=503, detail={"code": "SIM_UNREACHABLE", "message": str(e)[:200]}) from None


@app.get("/debug/state", dependencies=[Depends(require_internal)])
async def debug_state():
    """Phase 00: proxies /v1/instance. Phase 01: returns WorldState.to_public_json()."""
    r = await app.state.http.get("/v1/instance")
    return {"instance": r.json()}
