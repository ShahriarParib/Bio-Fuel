"""Alerts, risk and cover. Served from Redis documents the engine publishes every cycle."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from fsip_common.logging import get_logger

from ..auth import Actor, require_operator
from .decisions import _engine

log = get_logger()
router = APIRouter(prefix="/api", tags=["alerts"])


async def _doc(request: Request, key: str) -> Any:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return None
    try:
        raw = await redis.get(key)
        return json.loads(raw) if raw else None
    except Exception as err:
        log.warning("api.redis_read_failed", key=key, error=str(err))
        return None


def _unavailable(what: str) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": {"code": "CACHE_UNAVAILABLE",
                                                             "message": f"{what} not available yet"}})


@router.get("/alerts")
async def alerts(request: Request, status: str = Query("OPEN"), severity: str | None = None,
                 category: str | None = None):
    doc = await _doc(request, "fsip:alerts")
    if doc is None:
        return _unavailable("alerts")
    items = doc.get("open", []) if status in ("OPEN", "ACKNOWLEDGED") else doc.get("recent_resolved", [])
    if status == "ALL":
        items = doc.get("open", []) + doc.get("recent_resolved", [])
    # platform alerts from Prometheus/Alertmanager (category SYSTEM)
    from .system import system_alerts
    want = {"OPEN": ("OPEN",), "ACKNOWLEDGED": ("OPEN",), "ALL": ("OPEN", "RESOLVED")}.get(status, ("RESOLVED",))
    items = [*[a for a in await system_alerts(request) if a["status"] in want], *items]
    if severity:
        items = [a for a in items if a.get("severity") == severity]
    if category:
        items = [a for a in items if a.get("category") in category.split(",")]
    return {"items": items, "total": len(items)}


@router.post("/alerts/{alert_id}/ack")
async def ack(request: Request, alert_id: int, actor: Actor = Depends(require_operator)):
    return await _engine(request, "POST", f"/internal/alerts/{alert_id}/ack", {"actor": actor.name})


async def _state(request: Request) -> dict[str, Any] | None:
    return await _doc(request, "fsip:state")


@router.get("/risk")
async def risk(request: Request):
    st = await _state(request)
    if st is None:
        return _unavailable("risk")
    return {"tick": st.get("tick"), "items": st.get("risk", []), "forecast": st.get("forecast")}


@router.get("/cover")
async def cover(request: Request):
    st = await _state(request)
    if st is None or st.get("cover") is None:
        return _unavailable("cover")
    return {"tick": st.get("tick"), **st["cover"]}
