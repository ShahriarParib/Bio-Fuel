"""Station and depot inspection router."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fsip_common.logging import get_logger

log = get_logger()
router = APIRouter(prefix="/api", tags=["nodes"])


async def _get_current_state(request: Request) -> dict[str, Any] | None:
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client:
        try:
            raw = await redis_client.get("fsip:state")
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    return None


@router.get("/stations/{station_id}")
async def get_station(
    request: Request,
    station_id: str,
    history_ticks: int = Query(default=96, ge=1, le=1000),
):
    """Retrieve detailed state and recent telemetry for a fuel station."""
    state = await _get_current_state(request)
    station_obj = None

    if state and "stations" in state:
        for s in state["stations"]:
            if s.get("id") == station_id:
                station_obj = s
                break

    if station_obj is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"Station '{station_id}' not found"},
        )

    return {
        "station": station_obj,
        "history": [],
        "demand": [],
        "forecast": [],
    }


@router.get("/depots/{depot_id}")
async def get_depot(
    request: Request,
    depot_id: str,
    history_ticks: int = Query(default=96, ge=1, le=1000),
):
    """Retrieve detailed state and inventory projection for a depot."""
    state = await _get_current_state(request)
    depot_obj = None

    if state and "depots" in state:
        for d in state["depots"]:
            if d.get("id") == depot_id:
                depot_obj = d
                break

    if depot_obj is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"Depot '{depot_id}' not found"},
        )

    return {
        "depot": depot_obj,
        "history": [],
        "projection": [],
    }
