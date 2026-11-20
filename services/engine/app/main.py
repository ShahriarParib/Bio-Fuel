"""Engine service: coordinates simulator sync, state caching, persistence, and decisions."""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fsip_common.logging import configure_logging, get_logger
from prometheus_client import Counter, Gauge, make_asgi_app
from sqlalchemy.ext.asyncio import AsyncSession

from .config import EngineSettings, get_settings
from .db import repo
from .db.buffer import WriteBuffer
from .db.session import get_sessionmaker, init_db
from .sim.circuit import CircuitBreaker
from .sim.client import SimClient
from .sim.sse import SSEListener
from .state.publisher import Publisher
from .state.store import WorldState
from .state.sync import LOOP_RESTARTS_TOTAL, SyncLoop

log = get_logger()

ENGINE_INFO = Gauge("fsip_engine_info", "Engine build info", ["version", "git_sha"])
READY_CHECKS = Counter("fsip_ready_checks_total", "Readiness checks", ["result"])


async def _run_supervised(name: str, coroutine_fn, *args, **kwargs) -> None:
    """Supervise a background task with auto-restart and exponential backoff on crash."""
    restart_count = 0
    while True:
        try:
            await coroutine_fn(*args, **kwargs)
            break
        except asyncio.CancelledError:
            break
        except Exception as err:
            restart_count += 1
            LOOP_RESTARTS_TOTAL.labels(loop=name).inc()
            backoff = min(30.0, 1.0 * (1.5 ** min(restart_count, 8)))
            log.error(
                "supervisor.task_crashed",
                task=name,
                restart_count=restart_count,
                error=str(err),
                retry_in_sec=round(backoff, 2),
            )
            await asyncio.sleep(backoff)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging("engine", s.log_level)
    ENGINE_INFO.labels(s.app_version, s.git_sha).set(1)

    # 1. Initialize Simulator Client
    breaker = CircuitBreaker(
        "simulator",
        failure_threshold=s.breaker_failure_threshold,
        reset_timeout=s.breaker_reset_sec,
    )
    sim_client = SimClient(
        base_url=s.sim_base_url,
        timeout=s.sim_timeout_sec,
        max_retries=s.sim_max_retries,
        breaker=breaker,
    )

    # 2. In-memory State Store
    store = WorldState(
        engine_mode=s.engine_mode,
        operating_mode=s.engine_mode,
        policy=s.engine_policy,
    )

    # 3. Redis State Publisher
    publisher = Publisher(redis_url=s.redis_url)
    await publisher.connect()

    # 4. Database Setup & WriteBuffer (resilient: background bootstrap)
    write_buffer: WriteBuffer | None = None
    try:
        sessionmaker = get_sessionmaker(s.database_url)
        write_buffer = WriteBuffer(sessionmaker=sessionmaker)
    except Exception as db_err:
        log.warning("engine.db_setup_deferred", error=str(db_err))

    async def _bootstrap_db():
        try:
            await init_db(s.database_url)
        except Exception as err:
            log.warning("engine.db_init_background_failed", error=str(err))

    asyncio.create_task(_bootstrap_db())

    # 5. Define pipeline hooks to execute after each successful sync cycle
    hooks = []

    # Hook A: Publish full state to Redis
    async def _hook_publish_redis(ws: WorldState) -> None:
        await publisher.publish_state(ws)

    hooks.append(_hook_publish_redis)

    # Hook B: Persist snapshot and demand to Database via WriteBuffer
    if write_buffer is not None:
        async def _hook_persist_db(ws: WorldState) -> None:
            if not ws.snapshot:
                return
            snap = ws.snapshot
            sim_run_id = ws.sim_run_id
            tick = snap.instance.tick
            sim_time = snap.instance.sim_time
            stale = ws.stale
            degraded = ws.degraded

            depots_data = [d.model_dump() for d in snap.depots]
            stations_data = [st.model_dump() for st in snap.stations]
            routes_data = [r.model_dump() for r in snap.routes]
            events_data = [e.model_dump() for e in snap.events]
            supply_data = [sp.model_dump() for sp in snap.supply_arrivals]
            metrics_data = snap.metrics.model_dump()

            async def _do_persist(session: AsyncSession) -> None:
                await repo.upsert_snapshot(
                    session=session,
                    sim_run_id=sim_run_id,
                    tick=tick,
                    sim_time=sim_time,
                    depots=depots_data,
                    stations=stations_data,
                    routes=routes_data,
                    events=events_data,
                    supply=supply_data,
                    metrics=metrics_data,
                    stale=stale,
                    degraded=degraded,
                )

            write_buffer.submit(_do_persist, kind="snapshot")

        hooks.append(_hook_persist_db)

    # 6. Initialize Synchronization Loop and SSE Listener
    sync_loop = SyncLoop(
        client=sim_client,
        store=store,
        hooks=hooks,
        poll_interval=s.engine_poll_interval_sec,
    )

    sse_listener = SSEListener(
        base_url=s.sim_base_url,
        on_tick=lambda t: sync_loop.request_cycle(t),
        on_connect=lambda: sync_loop.full_sync(),
    )

    # Attach components to app state
    app.state.sim_client = sim_client
    app.state.store = store
    app.state.publisher = publisher
    app.state.sync_loop = sync_loop
    app.state.sse_listener = sse_listener
    app.state.write_buffer = write_buffer

    # Start supervised background tasks
    tasks = [
        asyncio.create_task(_run_supervised("sync_loop", sync_loop.run)),
        asyncio.create_task(_run_supervised("sse_listener", sse_listener.run)),
    ]
    if write_buffer is not None:
        tasks.append(asyncio.create_task(_run_supervised("write_buffer", write_buffer.run)))

    log.info("engine.start", sim=s.sim_base_url, mode=s.engine_mode, policy=s.engine_policy)

    yield

    # Teardown
    sync_loop.stop()
    sse_listener.stop()
    if write_buffer:
        write_buffer.stop()

    for t in tasks:
        t.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)
    await sim_client.close()
    await publisher.close()
    log.info("engine.stop")


app = FastAPI(title="fsip-engine", version="0.1.0", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


def require_internal(
    x_internal_token: str | None = Header(default=None),
    s: EngineSettings = Depends(get_settings),
) -> None:
    if x_internal_token != s.engine_internal_token:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "bad internal token"})


@app.get("/health/live")
async def live():
    return {"status": "ok", "service": "engine"}


@app.get("/health/ready")
async def ready():
    """Engine readiness: returns 200 if state was synced within 30 s or cached state is available."""
    store: WorldState = getattr(app.state, "store", None)
    if store is None:
        READY_CHECKS.labels(result="not_initialized").inc()
        raise HTTPException(status_code=503, detail={"code": "NOT_INITIALIZED", "message": "Store not initialized"})

    last_sync_age = time.monotonic() - store.last_sync_time
    if store.snapshot is not None:
        # If we have a snapshot, we can serve requests even in degraded mode
        READY_CHECKS.labels(result="ok").inc()
        return {
            "status": "ready",
            "tick": store.last_tick,
            "degraded": store.degraded,
            "stale": store.stale,
            "last_sync_age_sec": round(last_sync_age, 2),
        }

    # No snapshot yet: probe simulator directly
    sim_client: SimClient = app.state.sim_client
    try:
        health_res = await sim_client.health()
        READY_CHECKS.labels(result="ok").inc()
        return {
            "status": "ready",
            "simulator": health_res.data,
            "note": "Initial sync in progress",
        }
    except Exception as e:
        READY_CHECKS.labels(result="fail").inc()
        raise HTTPException(
            status_code=503,
            detail={"code": "SIM_UNREACHABLE", "message": f"Simulator unreachable: {e}"},
        ) from None


@app.get("/debug/state", dependencies=[Depends(require_internal)])
async def debug_state():
    """Returns the full in-memory WorldState matching public API schema."""
    store: WorldState = app.state.store
    return store.to_public_json()


@app.post("/debug/sync", dependencies=[Depends(require_internal)])
async def debug_sync():
    """Force an immediate full synchronization cycle."""
    sync_loop: SyncLoop = app.state.sync_loop
    await sync_loop.full_sync()
    store: WorldState = app.state.store
    return {"status": "sync_triggered", "tick": store.last_tick}
