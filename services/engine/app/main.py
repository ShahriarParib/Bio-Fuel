"""Engine service: coordinates simulator sync, state caching, persistence, and decisions."""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from fsip_common.logging import configure_logging, get_logger
from prometheus_client import Counter, Gauge, make_asgi_app
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .alerts.manager import AlertManager
from .config import EngineSettings, get_settings
from .db import repo
from .db.buffer import WriteBuffer
from .db.session import get_sessionmaker, init_db
from .decision.engine import DecisionEngine, DecisionError
from .intelligence.client import ForecastService
from .intelligence.pipeline import IntelligencePipeline
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
        engine_mode="NORMAL",  # health mode; ENGINE_MODE env is the operating mode (advisory | autopilot)
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

    db_ready = asyncio.Event()

    async def _bootstrap_db():
        try:
            await init_db(s.database_url)
            db_ready.set()
        except Exception as err:
            log.warning("engine.db_init_background_failed", error=str(err))

    db_task = asyncio.create_task(_bootstrap_db())

    async def _on_run_start(inst, is_reset: bool) -> None:
        """Resume or create the sim_run row; its id goes into every idempotency key."""
        previous = store.sim_run_id
        try:
            await asyncio.wait_for(db_ready.wait(), timeout=30)
            async with get_sessionmaker()() as session:
                run, resumed = await repo.resume_or_create_sim_run(
                    session,
                    scenario_id=inst.scenario_id,
                    seed=inst.seed,
                    tick_minutes=inst.tick_minutes,
                    tick=inst.tick,
                    previous_run_id=previous if is_reset else None,
                    is_reset=is_reset,
                )
            store.sim_run_id = run.id
            log.info("sim_run.ready", sim_run_id=run.id, resumed=resumed, reset=is_reset, tick=inst.tick)
        except Exception as err:
            # DB unavailable at boot: a time-based id still keeps idempotency keys unique; history writes for this
            # run will be retried by the buffer and dropped if the FK never resolves.
            store.sim_run_id = int(time.time())
            store.degraded_reasons.add("sim_run_unregistered")
            log.error("sim_run.fallback_id", sim_run_id=store.sim_run_id, error=str(err))

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

            rows = list(ws.new_demand_rows)
            if rows:
                async def _do_persist_demand(session: AsyncSession) -> None:
                    await repo.insert_demand_rows(session, sim_run_id=sim_run_id, observations=rows)

                write_buffer.submit(_do_persist_demand, kind="demand")

        hooks.append(_hook_persist_db)

    # Hook 0 (runs first, so the published state already contains the new recommendations): decide → act
    # the optimizer looks 96 ticks ahead plus 16 ticks of safety stock beyond each checkpoint
    forecast_service = ForecastService(s.intelligence_url, timeout=s.intelligence_timeout_sec,
                                       horizon=s.planning_horizon_ticks + s.safety_ticks + 8)
    decision_engine = DecisionEngine(
        store, sim_client, publisher, get_sessionmaker,
        ttl_ticks=s.decision_ttl_ticks, interval_ticks=s.engine_decision_interval_ticks,
        auto_max_line=s.auto_approve_max_liters, auto_min_confidence=s.auto_approve_min_confidence,
        reserve_fraction=s.depot_reserve_fraction, forecast_service=forecast_service,
    )
    alert_manager = AlertManager(publisher, get_sessionmaker)
    pipeline = IntelligencePipeline(forecast_service, alert_manager, publisher, decision_engine.planning_state)
    # order: observe/predict/detect → decide/act → publish → persist
    hooks.insert(0, decision_engine.on_cycle)
    hooks.insert(0, pipeline.on_cycle)
    app.state.decision_engine = decision_engine
    app.state.alerts = alert_manager
    app.state.forecast = forecast_service

    # 6. Initialize Synchronization Loop and SSE Listener
    sync_loop = SyncLoop(
        client=sim_client,
        store=store,
        hooks=hooks,
        poll_interval=s.engine_poll_interval_sec,
        on_run_start=_on_run_start,
    )

    sse_listener = SSEListener(
        base_url=s.sim_base_url,
        on_tick=lambda t: sync_loop.request_cycle(t),
        on_connect=lambda: sync_loop.full_sync(),
        # "Simulation reset" (or any notice) → resync now instead of waiting for the next poll
        on_notice=lambda _notice: sync_loop.full_sync(),
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

    db_task.cancel()
    for t in tasks:
        t.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)
    await sim_client.close()
    await publisher.close()
    await forecast_service.close()
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
            "decision_tick": app.state.decision_engine.last_cycle_tick,
            "policy": store.policy,
            "policy_used": app.state.decision_engine.last_policy_used,
            "fallback_reason": app.state.decision_engine.last_fallback_reason,
            "last_plan_ms": app.state.decision_engine.last_plan_ms,
            "operating_mode": store.operating_mode,
            "sim_run_id": store.sim_run_id,
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


# ---------------------------------------------------------------- decisions (internal; the api forwards operator calls)
class ApproveBody(BaseModel):
    actor: str = Field(min_length=1, max_length=64)
    role: str = "operator"
    line_ids: list[int] | None = None
    quantities: dict[str, float] | None = None
    reviewed_driver: bool = False


class RejectBody(BaseModel):
    actor: str = Field(min_length=1, max_length=64)
    reason_code: str = Field(
        default="OTHER", pattern="^(WRONG_PRIORITY|DATA_LOOKS_WRONG|PREFER_OTHER_ROUTE|NOT_NEEDED|OTHER)$"
    )
    comment: str = Field(default="", max_length=500)


class ActorBody(BaseModel):
    actor: str = Field(min_length=1, max_length=64)


class ModeBody(ActorBody):
    mode: str


class PolicyBody(ActorBody):
    policy: str


def _engine() -> DecisionEngine:
    return app.state.decision_engine


@app.exception_handler(DecisionError)
async def _decision_error(_request, err: DecisionError):
    return JSONResponse(status_code=err.status,
                        content={"detail": {"code": err.code, "message": err.message, "data": err.data}})


@app.get("/internal/decisions", dependencies=[Depends(require_internal)])
async def list_decisions(status: str | None = None, limit: int = Query(50, ge=1, le=200)):
    items = list(reversed(_engine().decisions.values()))
    if status:
        items = [d for d in items if d["status"] in status.split(",")]
    return {"items": items[:limit], "total": len(items)}


@app.get("/internal/decisions/{decision_id}", dependencies=[Depends(require_internal)])
async def get_decision(decision_id: int):
    return _engine().get(decision_id)


@app.post("/internal/decisions/{decision_id}/approve", dependencies=[Depends(require_internal)])
async def approve_decision(decision_id: int, body: ApproveBody):
    return await _engine().approve(decision_id, body.actor, body.line_ids, body.quantities)


@app.post("/internal/decisions/{decision_id}/reject", dependencies=[Depends(require_internal)])
async def reject_decision(decision_id: int, body: RejectBody):
    return await _engine().reject(decision_id, body.actor, body.reason_code, body.comment)


@app.post("/internal/plan-now", status_code=202, dependencies=[Depends(require_internal)])
async def plan_now(body: ActorBody):
    return await _engine().plan_now(body.actor)


@app.post("/internal/mode", dependencies=[Depends(require_internal)])
async def set_mode(body: ModeBody):
    return await _engine().set_mode(body.actor, body.mode)


@app.post("/internal/policy", dependencies=[Depends(require_internal)])
async def set_policy(body: PolicyBody):
    return await _engine().set_policy(body.actor, body.policy)


@app.post("/internal/alerts/{alert_id}/ack", dependencies=[Depends(require_internal)])
async def ack_alert(alert_id: int, body: ActorBody):
    a = app.state.alerts.acknowledge(alert_id, body.actor)
    if a is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "no open alert with this id"})
    await app.state.publisher.set_json("fsip:alerts", app.state.alerts.document())
    return a
