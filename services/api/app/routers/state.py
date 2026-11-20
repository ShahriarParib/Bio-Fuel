"""State and real-time event streaming router."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fsip_common.logging import get_logger
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger()
router = APIRouter(prefix="/api", tags=["state"])


@router.get("/state")
async def get_state(
    request: Request,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
):
    """Return the latest WorldState snapshot from Redis or database fallback."""
    redis_client = getattr(request.app.state, "redis", None)
    cached_json = None
    is_degraded = False

    # 1. Try Redis first
    if redis_client:
        try:
            cached_json = await redis_client.get("fsip:state")
        except Exception as e:
            log.warning("api.redis_state_read_failed", error=str(e))
            is_degraded = True

    state_dict: dict[str, Any] | None = None

    if cached_json:
        try:
            state_dict = json.loads(cached_json)
        except Exception:
            state_dict = None

    # 2. Fallback to Database if Redis missed or failed
    if state_dict is None:
        sessionmaker = getattr(request.app.state, "db_sessionmaker", None)
        if sessionmaker:
            try:
                from services.engine.app.db.models import WorldSnapshot  # type: ignore[import-not-found]
                async with sessionmaker() as session:
                    stmt = select(WorldSnapshot).order_by(desc(WorldSnapshot.sim_run_id), desc(WorldSnapshot.tick)).limit(1)
                    snap = (await session.execute(stmt)).scalar_one_or_none()
                    if snap:
                        state_dict = {
                            "sim_run_id": snap.sim_run_id,
                            "tick": snap.tick,
                            "sim_time": snap.sim_time.isoformat(),
                            "tick_minutes": 15,
                            "sim_status": "RUNNING",
                            "clock_label": f"t={snap.tick}",
                            "generated_at": snap.created_at.isoformat() if snap.created_at else "",
                            "age_seconds": 0.0,
                            "stale": snap.stale,
                            "degraded": True,
                            "degraded_reasons": ["redis_cache_miss_db_fallback"],
                            "engine_mode": "NORMAL",
                            "operating_mode": "advisory",
                            "policy": "optimizer",
                            "scarcity": {"DIESEL": False, "PETROL": False, "OCTANE": False},
                            "metrics": snap.metrics or {},
                            "depots": snap.depots or [],
                            "stations": snap.stations or [],
                            "routes": snap.routes or [],
                            "events": snap.events or [],
                            "supply_upcoming": snap.supply or [],
                            "allocations_active": [],
                            "alerts_open_count": {"INFO": 0, "WARNING": 0, "CRITICAL": 0},
                            "pending_recommendations": [],
                            "bottleneck": None,
                            "system": {
                                "simulator": "UP",
                                "sse": "UNKNOWN",
                                "intelligence": "UNKNOWN",
                                "db": "UP",
                                "redis": "DOWN",
                                "breakers": {"simulator": "CLOSED"},
                                "forecaster": "seasonal@builtin",
                            },
                        }
                        is_degraded = True
            except Exception as db_err:
                log.warning("api.db_state_fallback_failed", error=str(db_err))

    # 3. If still nothing available, return empty initial state
    if state_dict is None:
        state_dict = {
            "sim_run_id": 0,
            "tick": 0,
            "sim_status": "PAUSED",
            "degraded": True,
            "degraded_reasons": ["initial_sync_pending"],
            "stations": [],
            "depots": [],
            "routes": [],
            "events": [],
            "metrics": {},
        }
        is_degraded = True

    sim_run_id = state_dict.get("sim_run_id", 0)
    current_tick = state_dict.get("tick", 0)
    etag = f'"{sim_run_id}-{current_tick}"'

    if if_none_match and if_none_match == etag:
        return Response(status_code=304)

    resp = JSONResponse(content=state_dict)
    resp.headers["ETag"] = etag
    resp.headers["X-Data-Tick"] = str(current_tick)
    resp.headers["X-Degraded"] = "true" if is_degraded else "false"
    return resp


@router.get("/stream")
async def get_stream(
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    query_last_id: str | None = Query(default=None, alias="last_event_id"),
):
    """Server-Sent Events endpoint streaming domain updates from Redis stream."""
    redis_client = getattr(request.app.state, "redis", None)
    from_id = last_event_id or query_last_id or "$"

    async def _event_generator():
        current_id = from_id
        last_yield_time = time.monotonic()
        keepalive_timer = time.monotonic()

        while True:
            if await request.is_disconnected():
                break

            now = time.monotonic()
            # Send keepalive comment every 15 seconds
            if now - keepalive_timer >= 15.0:
                yield ": keepalive\n\n"
                keepalive_timer = now

            # 1. Read from Redis Stream if available
            events_read = []
            if redis_client:
                try:
                    res = await redis_client.xread(
                        {"fsip:domain-events": current_id},
                        count=10,
                        block=2000,
                    )
                    if res:
                        # res is list of [stream_name, [[msg_id, fields], ...]]
                        for _, messages in res:
                            for msg_id, fields in messages:
                                events_read.append((msg_id, fields))
                                current_id = msg_id
                except Exception as stream_err:
                    log.warning("api.stream_read_error", error=str(stream_err))
                    await asyncio.sleep(2.0)

            # 2. Dispatch events
            for msg_id, fields in events_read:
                event_type = fields.get("event_type", "state.updated")
                payload = json.dumps(fields)
                yield f"id: {msg_id}\nevent: {event_type}\ndata: {payload}\n\n"
                last_yield_time = time.monotonic()
                await asyncio.sleep(0.1)

            if not events_read:
                await asyncio.sleep(0.5)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
