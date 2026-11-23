"""State and real-time event streaming router."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fsip_common.logging import get_logger

log = get_logger()
router = APIRouter(prefix="/api", tags=["state"])


_NO_STATE = {"sim_run_id": 0, "tick": 0, "sim_status": "PAUSED", "degraded": True,
             "degraded_reasons": ["initial_sync_pending"], "stations": [], "depots": [], "routes": [], "events": [],
             "metrics": {}}


def _strip_etag(value: str) -> str:
    return value.strip().removeprefix("W/").strip('"')


@router.get("/state")
async def get_state(
    request: Request,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
):
    """The latest world state as the engine published it.

    Hot path (every open console polls this every second): compare the client's ETag with the small
    `fsip:state:etag` key first (304, no document read), otherwise return the cached JSON bytes as they are, with no
    parse and re-serialize. The ETag is a content hash, so a change of mode or health within a tick is never hidden.
    Redis down → the engine's in-memory state (no ETag: never cached).
    """
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is not None:
        try:
            if if_none_match:
                current = await redis_client.get("fsip:state:etag")
                if current and _strip_etag(if_none_match) == current:
                    return Response(status_code=304, headers={"ETag": f'"{current}"'})
            doc, etag, tick = await redis_client.mget("fsip:state", "fsip:state:etag", "fsip:state:tick")
            if doc:
                headers = {"X-Data-Tick": tick or "", "Cache-Control": "no-cache",
                           "X-Degraded": "true" if '"degraded": true' in doc else "false"}
                if etag:
                    headers["ETag"] = f'"{etag}"'
                return Response(content=doc, media_type="application/json", headers=headers)
        except Exception as e:
            log.warning("api.redis_state_read_failed", error=str(e))

    # Redis down (F10): ask the engine directly. It holds the live state in memory.
    from ..config import get_settings
    st = get_settings()
    state_dict: dict[str, Any] | None = None
    try:
        r = await request.app.state.http.get(f"{st.engine_internal_url}/debug/state", timeout=2.0,
                                             headers={"X-Internal-Token": st.engine_internal_token})
        if r.status_code == 200:
            state_dict = r.json()
            state_dict["degraded"] = True
            state_dict["degraded_reasons"] = [*state_dict.get("degraded_reasons", []),
                                              "cache down: state read from the engine directly"]
    except Exception as eng_err:
        log.warning("api.engine_state_fallback_failed", error=str(eng_err))
    if state_dict is None:
        state_dict = dict(_NO_STATE)
    return JSONResponse(content=state_dict, headers={"Cache-Control": "no-store", "X-Degraded": "true",
                                                     "X-Data-Tick": str(state_dict.get("tick", 0))})


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
        time.monotonic()
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
                time.monotonic()
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
