"""Recommendations, decisions and operator controls (21 §2.3, §2.5).

Reads come from the engine's Redis document `fsip:decisions` (so history stays visible when the engine is down and
reads scale with api replicas). Writes are forwarded to the engine, the only writer to the simulator.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fsip_common.logging import get_logger
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..auth import Actor, require_operator, require_supervisor
from ..config import get_settings

log = get_logger()
router = APIRouter(prefix="/api", tags=["decisions"])


class ApproveIn(BaseModel):
    line_ids: list[int] | None = Field(default=None, max_length=20)
    quantities: dict[str, float] | None = None
    reviewed_driver: bool = False


class RejectIn(BaseModel):
    reason_code: str = Field(default="OTHER",
                             pattern="^(WRONG_PRIORITY|DATA_LOOKS_WRONG|PREFER_OTHER_ROUTE|NOT_NEEDED|OTHER)$")
    comment: str = Field(default="", max_length=500)


class ModeIn(BaseModel):
    mode: str = Field(pattern="^(advisory|autopilot)$")


class PolicyIn(BaseModel):
    policy: str = Field(pattern="^(optimizer|heuristic)$")


async def _cached_decisions(request: Request) -> list[dict[str, Any]]:
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            raw = await redis.get("fsip:decisions")
            if raw:
                return json.loads(raw)
        except Exception as err:
            log.warning("api.decisions_cache_failed", error=str(err))
    # cache unavailable: ask the engine directly
    data = await _engine(request, "GET", "/internal/decisions?limit=200")
    return data.get("items", [])


async def _engine(request: Request, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    s = get_settings()
    http: httpx.AsyncClient = request.app.state.http
    try:
        resp = await http.request(method, f"{s.engine_internal_url}{path}", json=body,
                                  headers={"X-Internal-Token": s.engine_internal_token}, timeout=5.0)
    except httpx.HTTPError as err:
        raise HTTPException(status_code=503, detail={"code": "ENGINE_UNAVAILABLE",
                                                     "message": f"decision engine unreachable: {err}"}) from None
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail")
        except ValueError:
            detail = {"code": "ENGINE_ERROR", "message": resp.text[:200]}
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return resp.json()


@router.get("/recommendations")
async def recommendations(request: Request):
    items = [d for d in await _cached_decisions(request)
             if d.get("status") in ("PENDING_APPROVAL", "AWAITING_SECOND_APPROVAL")]
    return {"items": items}


@router.get("/decisions")
async def decisions(request: Request, status: str | None = None, policy: str | None = None,
                    from_tick: int | None = None, limit: int = Query(50, ge=1, le=200),
                    offset: int = Query(0, ge=0)):
    items = await _cached_decisions(request)
    if status:
        items = [d for d in items if d.get("status") in status.split(",")]
    if policy:
        items = [d for d in items if d.get("policy") == policy]
    if from_tick is not None:
        items = [d for d in items if d.get("tick", 0) >= from_tick]
    summary = [{k: v for k, v in d.items() if k not in ("explanation", "narrative")} for d in items]
    return {"items": summary[offset:offset + limit], "total": len(items)}


@router.get("/decisions/{decision_id}")
async def decision(request: Request, decision_id: int):
    for d in await _cached_decisions(request):
        if d.get("id") == decision_id:
            return d
    return await _engine(request, "GET", f"/internal/decisions/{decision_id}")


@router.post("/decisions/{decision_id}/approve")
async def approve(request: Request, decision_id: int, body: ApproveIn, actor: Actor = Depends(require_operator)):
    return await _engine(request, "POST", f"/internal/decisions/{decision_id}/approve",
                         {"actor": actor.name, "role": actor.role, **body.model_dump()})


@router.post("/decisions/{decision_id}/reject")
async def reject(request: Request, decision_id: int, body: RejectIn, actor: Actor = Depends(require_operator)):
    return await _engine(request, "POST", f"/internal/decisions/{decision_id}/reject",
                         {"actor": actor.name, **body.model_dump()})


@router.post("/plan-now", status_code=202)
async def plan_now(request: Request, actor: Actor = Depends(require_operator)):
    return await _engine(request, "POST", "/internal/plan-now", {"actor": actor.name})


@router.post("/control/mode")
async def control_mode(request: Request, body: ModeIn, actor: Actor = Depends(require_operator)):
    return await _engine(request, "POST", "/internal/mode", {"actor": actor.name, "mode": body.mode})


@router.post("/control/policy")
async def control_policy(request: Request, body: PolicyIn, actor: Actor = Depends(require_supervisor)):
    return await _engine(request, "POST", "/internal/policy", {"actor": actor.name, "policy": body.policy})


@router.get("/audit")
async def audit(request: Request, limit: int = Query(100, ge=1, le=500)):
    sm = getattr(request.app.state, "db_sessionmaker", None)
    if sm is None:
        return JSONResponse(status_code=503, content={"detail": {"code": "DB_UNAVAILABLE", "message": "no database"}})
    try:
        async with sm() as session:
            rows = (await session.execute(
                text("SELECT id, at, actor, action, entity_type, entity_id, payload FROM audit_log "
                     "ORDER BY id DESC LIMIT :limit"), {"limit": limit})).mappings().all()
    except Exception as err:
        return JSONResponse(status_code=503, content={"detail": {"code": "DB_UNAVAILABLE", "message": str(err)}})
    return {"items": [{**dict(r), "at": r["at"].isoformat() if r["at"] else None} for r in rows]}
